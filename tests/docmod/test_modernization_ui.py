# CUI // SP-CTI
"""docmod hitl-04/05/06 + ux-01/02: modernization routes, resolve flow,
batch ingest, daemon guard, IQE collection, template markup."""
from __future__ import annotations

import io
import json
import uuid
from pathlib import Path

import flask
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

_DOCMOD_DDL_KEYS = ("docmod_findings", "docmod_scan_runs")


@pytest.fixture()
def db():
    from tests.conftest import MINIMAL_ICDEV_SCHEMA
    from tools.db.storage import get_connection

    conn = get_connection()
    for stmt in MINIMAL_ICDEV_SCHEMA.split(";"):
        if any(k in stmt for k in _DOCMOD_DDL_KEYS) and "CREATE TABLE" in stmt:
            conn.execute(stmt)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS dic_documents (doc_id TEXT PRIMARY KEY, "
        "collection_id TEXT, title TEXT, tenant_id TEXT, classification TEXT, created_at TEXT)"
    )
    # cef-ui-03: /resolve now audits the human disposition fail-closed, so the
    # append-only audit_trail is part of this route's substrate. Kept in step
    # with tests/conftest.py's audit_trail, which mirrors the LIVE PG shape.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS audit_trail ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, "
        "event_type TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, "
        "details TEXT, affected_files TEXT, classification TEXT DEFAULT 'CUI', "
        "ip_address TEXT, session_id TEXT, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
        "hash TEXT, previous_hash TEXT, signature TEXT)"
    )
    conn.execute("DELETE FROM audit_trail")
    conn.execute("DELETE FROM docmod_findings")
    if not conn.execute("SELECT run_id FROM docmod_scan_runs WHERE run_id='run-ui'").fetchone():
        conn.execute(
            "INSERT INTO docmod_scan_runs (run_id, scope_type, started_at) "
            "VALUES ('run-ui','doc','2026-07-10T00:00:00')"
        )
    conn.commit()
    conn.close()
    yield


@pytest.fixture()
def client():
    import tools.document_intelligence.blueprint as bp_mod  # registers all routes
    import tools.document_intelligence.modernization_routes  # noqa: F401

    app = flask.Flask(__name__)
    app.register_blueprint(bp_mod.dic_bp, url_prefix="/document-intelligence")
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _seed_finding(doc_id: str, **overrides) -> str:
    from tools.db.storage import get_connection

    fid = f"fnd-{uuid.uuid4().hex[:12]}"
    row = {
        "finding_type": "deprecated_tech", "currency_verdict": "deprecated",
        "severity": "high", "entity_label": "TLS 1.1",
        "section_heading": "Security", "state": "open",
        "rationale": "TLS 1.1 is deprecated.",
        "evidence_json": json.dumps([{"source": "rule:crypto-tls-02", "detail": "x", "date": ""}]),
        "recommended_replacement": "TLS 1.2 or higher",
        "confidence": 1.0,
    }
    row.update(overrides)
    conn = get_connection()
    conn.execute(
        """INSERT INTO docmod_findings
           (finding_id, run_id, doc_id, version_id, pack_id, entity_label, entity_type,
            finding_type, currency_verdict, severity, rationale, evidence_json,
            recommended_replacement, confidence, state, dedupe_key, section_heading, created_at)
           VALUES (%s,'run-ui',%s,'v1','crypto_protocols',%s,'protocol',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'2026-07-10T00:00:00')""",
        (fid, doc_id, row["entity_label"], row["finding_type"], row["currency_verdict"],
         row["severity"], row["rationale"], row["evidence_json"],
         row["recommended_replacement"], row["confidence"], row["state"],
         f"dk-{uuid.uuid4().hex[:8]}", row["section_heading"]),
    )
    conn.commit()
    conn.close()
    return fid


def test_summary_counts_and_worst_first(client, db):
    d1, d2 = f"doc-{uuid.uuid4().hex[:6]}", f"doc-{uuid.uuid4().hex[:6]}"
    _seed_finding(d1)
    _seed_finding(d1, entity_label="Catalyst 6500", finding_type="eol_hardware")
    _seed_finding(d2)

    data = client.get("/document-intelligence/api/modernization/summary").get_json()
    assert data["total_open"] == 3
    assert data["by_type"]["deprecated_tech"] == 2
    assert data["by_type"]["eol_hardware"] == 1
    assert data["documents"][0]["doc_id"] == d1  # worst-first


def test_doc_findings_feed(client, db):
    doc_id = f"doc-{uuid.uuid4().hex[:6]}"
    _seed_finding(doc_id)
    rows = client.get(
        f"/document-intelligence/api/modernization/doc/{doc_id}/findings"
    ).get_json()
    assert len(rows) == 1
    f = rows[0]
    assert f["section_heading"] == "Security"
    assert f["recommended_replacement"] == "TLS 1.2 or higher"
    assert f["evidence"] and f["evidence"][0]["source"] == "rule:crypto-tls-02"


def test_resolve_appends_state_row(client, db):
    doc_id = f"doc-{uuid.uuid4().hex[:6]}"
    fid = _seed_finding(doc_id)
    resp = client.post(
        f"/document-intelligence/api/modernization/findings/{fid}/resolve",
        json={"disposition": "accepted"},
    )
    assert resp.status_code == 200, resp.get_json()

    from tools.doc_modernization import get_findings
    latest = get_findings(doc_id=doc_id)
    assert len(latest) == 1 and latest[0]["state"] == "accepted"
    assert latest[0]["supersedes_id"] == fid  # append-only chain

    # cef-ui-03: the state row alone records WHAT was decided, never WHO decided
    # it — docmod_findings has no reviewer column. The audit row is the record
    # that a human, and not the sweep, disposed of this finding.
    from tools.db.storage import get_connection
    conn = get_connection()
    audits = [dict(r) for r in conn.execute(
        "SELECT event_type, actor, action FROM audit_trail ORDER BY id").fetchall()]
    conn.close()
    assert [a["action"] for a in audits] == ["docmod_finding.accepted"], audits
    assert audits[0]["event_type"] == "dic.hitl_decision"

    assert client.post(
        f"/document-intelligence/api/modernization/findings/{fid}/resolve",
        json={"disposition": "bogus"},
    ).status_code == 400


def test_bulk_validates_input(client, db):
    assert client.post("/document-intelligence/api/modernization/bulk",
                       json={"action": "nope", "doc_ids": ["d"]}).status_code == 400
    assert client.post("/document-intelligence/api/modernization/bulk",
                       json={"action": "rescan", "doc_ids": []}).status_code == 400


def test_bulk_rescan_dispatches(client, db, monkeypatch):
    import tools.doc_modernization.scanner as scanner

    calls = []
    monkeypatch.setattr(scanner, "scan_document",
                        lambda d, force=False, **kw: calls.append((d, force)) or {"scanned": True})
    doc_id = f"doc-{uuid.uuid4().hex[:6]}"
    resp = client.post("/document-intelligence/api/modernization/bulk",
                       json={"action": "rescan", "doc_ids": [doc_id]})
    assert resp.status_code == 200
    assert calls == [(doc_id, True)]


def test_batch_ingest_requires_files_and_streams(client, db, monkeypatch):
    assert client.post("/document-intelligence/api/ingest/batch").status_code == 400

    import tools.document_intelligence.ingest_orchestrator as orch

    def fake_batch(files, collection_id, progress_cb=None, **kw):
        for i, f in enumerate(files, 1):
            progress_cb(i, len(files), [])
        return type("R", (), {"succeeded": len(files), "failed": 0})()

    monkeypatch.setattr(orch, "ingest_batch", fake_batch)
    resp = client.post(
        "/document-intelligence/api/ingest/batch",
        data={
            "collection_id": "legacy",
            "files": [
                (io.BytesIO(b"doc one"), "a.txt"),
                (io.BytesIO(b"doc two"), "b.txt"),
            ],
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 202, resp.get_json()
    data = resp.get_json()
    assert data["files"] == 2
    assert data["progress_url"].endswith("/stream")

    # progress events landed on the shared job queue
    import time
    from tools.document_intelligence.blueprint import _JOB_QUEUES
    q = _JOB_QUEUES.get(data["job_id"])
    assert q is not None
    events = []
    deadline = time.time() + 5
    while time.time() < deadline and len(events) < 3:
        try:
            events.append(json.loads(q.get(timeout=1)))
        except Exception:
            break
    assert any(e.get("done") for e in events)
    assert any(e.get("of") == 2 for e in events)


def _run_batch(client, monkeypatch, form_extra=None):
    """POST a 1-file batch and return the kwargs the route handed ingest_batch."""
    import time

    import tools.document_intelligence.ingest_orchestrator as orch
    from tools.document_intelligence.blueprint import _JOB_QUEUES

    captured = {}

    def fake_batch(files, collection_id, progress_cb=None, **kw):
        captured.update(kw)
        progress_cb(1, 1, [])
        return type("R", (), {"succeeded": 1, "failed": 0})()

    monkeypatch.setattr(orch, "ingest_batch", fake_batch)
    data = {"collection_id": "legacy", "files": [(io.BytesIO(b"doc one"), "a.txt")]}
    data.update(form_extra or {})
    resp = client.post("/document-intelligence/api/ingest/batch", data=data,
                       content_type="multipart/form-data")
    assert resp.status_code == 202, resp.get_json()

    # The route ingests on a background thread; draining the job queue until the
    # terminal event is how the other batch test synchronises.
    q = _JOB_QUEUES.get(resp.get_json()["job_id"])
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            if json.loads(q.get(timeout=1)).get("done"):
                break
        except Exception:
            break
    return captured


def test_batch_ingest_embeds_and_bridges_kg_by_default(client, db, monkeypatch):
    """Regression: the bulk front door silently produced unusable documents.

    ingest_batch defaults every enrichment off — correct for a primitive the
    caller drives deliberately. But this route passed neither embed nor
    bridge_kg, so 'ingest my documents' returned 202, reported success, and left
    documents with no embeddings (invisible to vector search) and no KG entities
    (invisible to /explorer and /analytics). Nothing failed; they just did not
    work. Before the fix both assertions below are False.
    """
    kw = _run_batch(client, monkeypatch)
    assert kw.get("embed") is True, "bulk-ingested documents must be searchable"
    assert kw.get("bridge_kg") is True, "bulk-ingested documents must reach the KG"


def test_batch_ingest_enrichment_is_opt_out_not_silent(client, db, monkeypatch):
    """The cheap path stays reachable — but only when the caller asks for it."""
    kw = _run_batch(client, monkeypatch, {"embed": "false", "bridge_kg": "0"})
    assert kw.get("embed") is False
    assert kw.get("bridge_kg") is False


def test_batch_ingest_leaves_per_file_llm_enrichment_off(client, db, monkeypatch):
    """summarize/metadata/correspondence are genuinely expensive per document,
    and their absence does not stop a document being found. They stay off."""
    kw = _run_batch(client, monkeypatch)
    for expensive in ("summarize", "extract_metadata", "extract_correspondence"):
        assert kw.get(expensive) in (None, False)


def test_daemon_guard_and_templates():
    app_py = (REPO_ROOT / "tools" / "dashboard" / "app.py").read_text(encoding="utf-8")
    assert "ICDEV_DIC_FRESHNESS_SCAN_HOURS" in app_py
    assert "dic-freshness-scan-daemon" in app_py
    assert "PYTEST_CURRENT_TEST" in app_py  # never starts under pytest

    fresh = (REPO_ROOT / "tools" / "dashboard" / "templates" / "document_intelligence"
             / "freshness.html").read_text(encoding="utf-8")
    assert "Freshness &amp; Modernization" in fresh
    assert "bulkAction" in fresh and "queue_redlines" in fresh
    assert "'%.0f'|format" not in fresh  # Jinja guardrail

    detail = (REPO_ROOT / "tools" / "dashboard" / "templates" / "document_intelligence"
              / "doc_detail.html").read_text(encoding="utf-8")
    assert "mod-badges" in detail and "resolveFinding" in detail

    review = (REPO_ROOT / "tools" / "dashboard" / "templates" / "document_intelligence"
              / "review.html").read_text(encoding="utf-8")
    assert "panel-modernization" in review and "tab-mod" in review


def test_iqe_collection_and_seed_queries(db):
    from tools.iqe.executor import Executor  # noqa: F401 — ensure import path
    import tools.iqe.adapters.dic as dic_adapter

    doc_id = f"doc-{uuid.uuid4().hex[:6]}"
    _seed_finding(doc_id)
    rows = dic_adapter.modernization_findings_adapter(None)
    assert any(r["doc_id"] == doc_id for r in rows)

    qdir = REPO_ROOT / "context" / "iqe" / "queries" / "dic"
    names = {p.name for p in qdir.glob("2*.iqe")}
    assert {"20_open_modernization_findings.iqe", "21_redlines_pending_review.iqe",
            "22_finding_counts_by_type.iqe"} <= names


def test_docmod_redline_llm_routing_is_config_driven():
    """User rule: provider selection comes from args/llm_config.yaml, never
    hardcoded — an unrouted function falls to 'default' and can surprise
    non-Anthropic deployments (e.g. Ollama/Kimi) with wrong-provider key
    prompts when the chain walks to a cloud fallback."""
    import yaml

    from tools.llm.config_path import resolve_llm_config_path

    cfg = yaml.safe_load(Path(resolve_llm_config_path()).read_text(encoding="utf-8"))
    route = (cfg.get("routing") or {}).get("docmod_redline")
    assert route and route.get("chain"), "docmod_redline missing from llm_config routing"
    # primary must be the configured cloud/local default, not a hardcoded vendor
    assert route["chain"][0] in ("kimi-cloud", "qwen3-local"), route["chain"]

    # and the drafter must invoke through the router with that function name
    src = (REPO_ROOT / "tools" / "doc_modernization" / "redline_drafter.py").read_text(
        encoding="utf-8"
    )
    assert 'invoke("docmod_redline"' in src
    assert "ANTHROPIC_API_KEY" not in src  # no vendor-specific env handling
