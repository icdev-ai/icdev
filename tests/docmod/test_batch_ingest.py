# CUI // SP-CTI
"""docmod-ux-02: bulk legacy-doc ingest route + job lifecycle + empty states.

No real embeddings — ingest_batch is monkeypatched with a fake that drives the
progress callback exactly like the real orchestrator does.
"""
from __future__ import annotations

import io
import time
from pathlib import Path

import flask
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def dic_client():
    from tools.document_intelligence.blueprint import dic_bp

    app = flask.Flask(
        __name__,
        template_folder=str(REPO_ROOT / "tools" / "dashboard" / "templates"),
    )
    app.register_blueprint(dic_bp)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


class _FakeBatchResult:
    def __init__(self, paths):
        self.total = len(paths)
        self.succeeded = len(paths)
        self.failed = 0
        self.per_file = [
            {"path": str(p), "ok": True, "doc_id": f"doc-{i}", "elapsed_s": 0.01,
             "anomalous": False, "error": ""}
            for i, p in enumerate(paths)
        ]
        self.anomalous_paths = []


def _fake_ingest_batch(files, collection_id, *, progress_cb=None, **kwargs):
    for i, _ in enumerate(files):
        if progress_cb:
            progress_cb(i + 1, len(files), [])
    return _FakeBatchResult(files)


def _drain_queue(job_id, timeout=10.0):
    """Collect all SSE events for a job directly off the in-memory queue."""
    import queue as _q

    from tools.document_intelligence.blueprint import _JOB_QUEUES

    q = _JOB_QUEUES.get(job_id)
    assert q is not None, "job queue not registered"
    events = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            ev = q.get(timeout=0.5)
        except _q.Empty:
            continue
        if ev is None:
            return events
        events.append(ev)
    raise AssertionError(f"job {job_id} did not finish; got {events}")


# ── route ────────────────────────────────────────────────────────────────────

def test_batch_ingest_no_files_is_400(dic_client):
    resp = dic_client.post("/document-intelligence/api/ingest/batch", data={})
    assert resp.status_code == 400
    assert "no files" in resp.get_json()["error"]


def test_batch_ingest_three_files_full_lifecycle(dic_client, monkeypatch):
    import tools.document_intelligence.ingest_orchestrator as orch

    monkeypatch.setattr(orch, "ingest_batch", _fake_ingest_batch)

    data = {
        "collection_id": "legacy",
        "files": [
            (io.BytesIO(b"alpha content"), "alpha.txt"),
            (io.BytesIO(b"beta content"), "beta.md"),
            (io.BytesIO(b"gamma content"), "gamma.txt"),
        ],
    }
    resp = dic_client.post(
        "/document-intelligence/api/ingest/batch",
        data=data,
        content_type="multipart/form-data",
    )
    assert resp.status_code == 202, resp.get_json()
    body = resp.get_json()
    assert body["files"] == 3
    assert body["filenames"] == ["alpha.txt", "beta.md", "gamma.txt"]
    assert body["collection_id"] == "legacy"
    job_id = body["job_id"]
    assert body["stream_url"].endswith(f"/api/ingest/{job_id}/stream")

    events = _drain_queue(job_id)
    per_file = [e for e in events if e.get("stage") == "batch_file"]
    assert len(per_file) == 3
    assert per_file[0] == {
        "stage": "batch_file", "file": 1, "of": 3,
        "filename": "alpha.txt", "status": "ingested", "pct": 33,
    }
    assert [e["filename"] for e in per_file] == ["alpha.txt", "beta.md", "gamma.txt"]

    done = [e for e in events if e.get("stage") == "done"]
    assert len(done) == 1
    assert done[0]["succeeded"] == 3
    assert done[0]["failed"] == 0
    assert done[0]["total"] == 3
    assert "chunks" in done[0]  # terminal condition for the shared SSE stream

    # Result endpoint serves the in-memory cache.
    from tools.document_intelligence.blueprint import _JOB_RESULTS
    deadline = time.monotonic() + 5
    while job_id not in _JOB_RESULTS and time.monotonic() < deadline:
        time.sleep(0.05)
    res = dic_client.get(f"/document-intelligence/api/ingest/{job_id}/result")
    assert res.status_code == 200
    payload = res.get_json()
    assert payload["status"] == "done"
    assert payload["succeeded"] == 3
    assert len(payload["per_file"]) == 3


def test_batch_ingest_error_path(dic_client, monkeypatch):
    import tools.document_intelligence.ingest_orchestrator as orch

    def _boom(files, collection_id, **kwargs):
        raise RuntimeError("extractor unavailable")

    monkeypatch.setattr(orch, "ingest_batch", _boom)

    resp = dic_client.post(
        "/document-intelligence/api/ingest/batch",
        data={"files": [(io.BytesIO(b"x"), "only.txt")]},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 202
    job_id = resp.get_json()["job_id"]
    events = _drain_queue(job_id)
    errs = [e for e in events if e.get("stage") == "error"]
    assert errs and "extractor unavailable" in errs[0]["message"]


def test_batch_route_registered_via_one_import_line():
    bp_src = (REPO_ROOT / "tools" / "document_intelligence" / "blueprint.py").read_text(
        encoding="utf-8"
    )
    assert "from tools.document_intelligence import modernization_routes" in bp_src


# ── empty states ─────────────────────────────────────────────────────────────

def _render_capture(monkeypatch):
    """Swap render_template for a recorder — base.html needs app context
    processors (nav_tree) that only the real dashboard app provides."""
    import tools.document_intelligence.blueprint as bp

    calls = []

    def _fake_render(template, **ctx):
        calls.append((template, ctx))
        return "ok"

    monkeypatch.setattr(bp, "render_template", _fake_render)
    return calls


def test_index_and_freshness_pass_doc_count(dic_client, monkeypatch):
    import tools.document_intelligence.blueprint as bp

    calls = _render_capture(monkeypatch)
    monkeypatch.setattr(bp, "_corpus_doc_count", lambda tenant_id: 0)
    dic_client.get("/document-intelligence/")
    dic_client.get("/document-intelligence/freshness")
    assert calls[0][0] == "document_intelligence/index.html"
    assert calls[0][1]["doc_count"] == 0
    assert calls[1][0] == "document_intelligence/freshness.html"
    assert calls[1][1]["doc_count"] == 0

    monkeypatch.setattr(bp, "_corpus_doc_count", lambda tenant_id: 42)
    dic_client.get("/document-intelligence/")
    assert calls[2][1]["doc_count"] == 42


def test_empty_state_markup_is_guarded_by_doc_count():
    tpl_dir = REPO_ROOT / "tools" / "dashboard" / "templates" / "document_intelligence"

    idx = (tpl_dir / "index.html").read_text(encoding="utf-8")
    assert "{% if doc_count == 0 %}" in idx
    assert idx.index("{% if doc_count == 0 %}") < idx.index("dic-onboarding")
    for step in ("Bulk-upload legacy docs", "Run a staleness scan",
                 "Review modernization findings"):
        assert step in idx, f"onboarding step {step!r} missing from index"

    fresh = (tpl_dir / "freshness.html").read_text(encoding="utf-8")
    assert "{% if doc_count == 0 %}" in fresh
    assert fresh.index("{% if doc_count == 0 %}") < fresh.index("freshness-onboarding")
    for step in ("Bulk-upload legacy docs", "Run a staleness scan",
                 "Review modernization findings"):
        assert step in fresh, f"onboarding step {step!r} missing from freshness"


def test_templates_carry_bulk_ingest_ui():
    tpl_dir = REPO_ROOT / "tools" / "dashboard" / "templates" / "document_intelligence"
    idx = (tpl_dir / "index.html").read_text(encoding="utf-8")
    assert "/api/ingest/batch" in idx
    assert "freshness-cta" in idx
    assert "Run a freshness scan on these" in idx

    cols = (tpl_dir / "collections.html").read_text(encoding="utf-8")
    assert "bulk-file-input" in cols and "multiple" in cols
    assert "/api/ingest/batch" in cols
    assert "Run a freshness scan on these" in cols
