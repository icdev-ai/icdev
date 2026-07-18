# CUI // SP-CTI
"""Tests for the Cortex REST API v1 (ctx-expose-02).

Exercises the six ``/cortex/api/v1/*`` endpoints through a Flask test client
with the underlying facade functions monkeypatched, so the tests cover the
HTTP contract — schema-stable JSON, governance-block envelopes, authentication
rejection, and server-side tenant derivation — without a live LLM router or DB.

Auth is simulated by an app-level ``before_request`` that sets ``g.current_user``
and ``g.security_context`` (the exact seam the dashboard auth middleware fills).
Leaving them unset simulates an unauthenticated caller.
"""
from __future__ import annotations

import pytest
from flask import Flask, g

# The /cortex/api/v1/* handlers live in tools.cortex.rest_v1 (folded onto the
# canvas blueprint via register_rest_v1). Patch the facades where they are USED
# — in rest_v1 — not on the blueprint module.
import tools.cortex.rest_v1 as bp
from tools.cortex.analyst import CortexAnalystError, CortexQueryBlocked
from tools.cortex.blueprint import cortex_bp
from tools.cortex.governance import GovernanceBlockedError
from tools.cortex.schemas import (
    Citation,
    CortexResult,
    CortexSearchResult,
    GovernanceReport,
)

SESSION_TENANT = "tenant-real"
SESSION_USER = "user-real"
SPOOFED_TENANT = "tenant-evil"


def make_client(authed: bool = True, tenant: str = SESSION_TENANT):
    app = Flask(__name__)
    app.register_blueprint(cortex_bp)

    @app.before_request
    def _simulate_auth():
        if authed:
            g.current_user = {"id": SESSION_USER, "role": "admin", "tenant_id": tenant}
            g.security_context = {
                "tenant_id": tenant,
                "user_id": SESSION_USER,
                "classification": "CUI",
            }
        # unauthenticated: leave g.current_user unset

    return app.test_client()


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class CapturingPipeline:
    """Stand-in for GovernancePipeline that records ctx and can force a block."""

    captured: list = []
    block: bool = False

    def __init__(self, operation="cortex", agent_id="cortex"):
        self.operation = operation

    def wrap(self, fn, ctx=None, *, prompt="", context_sources=None, retrieval=True):
        CapturingPipeline.captured.append(
            {
                "operation": self.operation,
                "ctx": ctx,
                "prompt": prompt,
                "retrieval": retrieval,
                "context_sources": context_sources,
            }
        )
        if CapturingPipeline.block:
            report = GovernanceReport(
                gates_run=["pre_check"],
                outcomes={"pre_check": "fail"},
                blocked=True,
                blocked_reason="blocked by test pre-check",
            )
            raise GovernanceBlockedError("pre_check", "blocked by test pre-check", report)
        result = fn(prompt)
        report = GovernanceReport(
            gates_run=["pre_check", "operation"],
            outcomes={"pre_check": "pass", "operation": "pass"},
        )
        if isinstance(result, CortexResult):
            result.governance = report
        return result, report


@pytest.fixture(autouse=True)
def _reset_pipeline():
    CapturingPipeline.captured = []
    CapturingPipeline.block = False
    yield


@pytest.fixture
def patch_pipeline(monkeypatch):
    monkeypatch.setattr(bp, "GovernancePipeline", CapturingPipeline)
    return CapturingPipeline


def _sample_search_result():
    return CortexSearchResult(
        content="hit content",
        score=0.9,
        backend="rag",
        strategy="auto:factual[rag]",
        citation=Citation(source_id="1", source_type="rag_chunk"),
    )


def _sample_cortex_result():
    return CortexResult(text="answer", provider="fake", model="m", latency_ms=5)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
ENDPOINTS = [
    "/cortex/api/v1/search",
    "/cortex/api/v1/ask",
    "/cortex/api/v1/complete",
    "/cortex/api/v1/reason",
    "/cortex/api/v1/classify",
    "/cortex/api/v1/extract",
    "/cortex/api/v1/govern",
]


@pytest.mark.parametrize("path", ENDPOINTS)
def test_unauthenticated_requests_rejected(path):
    client = make_client(authed=False)
    resp = client.post(path, json={"query": "x", "question": "x", "prompt": "x",
                                   "text": "x", "labels": ["a"], "schema": {"a": 1}})
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "authentication required"


# ---------------------------------------------------------------------------
# Happy paths — schema-stable JSON
# ---------------------------------------------------------------------------
def test_search_returns_normalized_rows(monkeypatch):
    captured = {}

    def fake_search(query, top_k=5, ctx=None, strategy="auto", config=None):
        captured["ctx"] = ctx
        captured["strategy"] = strategy
        captured["top_k"] = top_k
        return [_sample_search_result()]

    monkeypatch.setattr(bp, "search", fake_search)
    client = make_client()
    resp = client.post("/cortex/api/v1/search", json={"query": "find things", "top_k": 3})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["count"] == 1
    row = body["results"][0]
    assert set(row) == {"content", "score", "backend", "strategy", "citation", "raw_scores", "metadata"}
    assert captured["top_k"] == 3


def test_ask_returns_cortex_result(monkeypatch):
    def fake_ask(question, mode="auto", ctx=None, canvas=None, collections=None, summarize=False):
        return _sample_cortex_result()

    monkeypatch.setattr(bp, "ask", fake_ask)
    client = make_client()
    resp = client.post("/cortex/api/v1/ask", json={"question": "how many?"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body) == {"text", "citations", "governance", "provider", "model",
                         "cost", "latency_ms", "input_tokens", "output_tokens",
                         "grounded", "data", "metadata"}
    assert body["text"] == "answer"


def test_complete_is_governed(monkeypatch, patch_pipeline):
    monkeypatch.setattr(bp, "complete", lambda prompt, ctx=None, **kw: _sample_cortex_result())
    client = make_client()
    resp = client.post("/cortex/api/v1/complete", json={"prompt": "draft a note"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["text"] == "answer"
    assert body["governance"]["gates_run"] == ["pre_check", "operation"]
    assert patch_pipeline.captured[0]["operation"] == "cortex.complete"
    assert patch_pipeline.captured[0]["retrieval"] is False


def test_reason_is_governed(monkeypatch, patch_pipeline):
    captured = {}

    def _fake_reason(prompt, ctx=None, **kw):
        captured.update(kw)
        return CortexResult(text=f"{kw.get('mode')} answer")

    monkeypatch.setattr(bp, "reason", _fake_reason)
    client = make_client()
    resp = client.post("/cortex/api/v1/reason",
                       json={"prompt": "design a cache", "mode": "council"})
    assert resp.status_code == 200
    assert resp.get_json()["text"] == "council answer"
    assert captured["mode"] == "council"
    assert patch_pipeline.captured[0]["operation"] == "cortex.reason"


def test_reason_rejects_bad_mode(monkeypatch, patch_pipeline):
    monkeypatch.setattr(bp, "reason", lambda prompt, ctx=None, **kw: CortexResult(text="x"))
    client = make_client()
    resp = client.post("/cortex/api/v1/reason", json={"prompt": "q", "mode": "telepathy"})
    assert resp.status_code == 400  # validation error envelope


def test_classify_is_governed(monkeypatch, patch_pipeline):
    monkeypatch.setattr(bp, "classify", lambda text, labels, ctx=None: CortexResult(text=labels[0]))
    client = make_client()
    resp = client.post("/cortex/api/v1/classify", json={"text": "a bug", "labels": ["bug", "feature"]})
    assert resp.status_code == 200
    assert resp.get_json()["text"] == "bug"
    assert patch_pipeline.captured[0]["operation"] == "cortex.classify"


def test_extract_is_governed(monkeypatch, patch_pipeline):
    monkeypatch.setattr(bp, "extract", lambda text, schema, ctx=None: CortexResult(text='{"n": 1}'))
    client = make_client()
    resp = client.post("/cortex/api/v1/extract", json={"text": "one", "schema": {"type": "object"}})
    assert resp.status_code == 200
    assert resp.get_json()["text"] == '{"n": 1}'
    assert patch_pipeline.captured[0]["operation"] == "cortex.extract"


def test_govern_returns_report(patch_pipeline):
    client = make_client()
    resp = client.post("/cortex/api/v1/govern", json={"text": "some text to govern", "retrieval": False})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["text"] == "some text to govern"
    assert body["blocked"] is False
    assert set(body) == {"text", "grounded", "blocked", "governance"}
    assert patch_pipeline.captured[0]["operation"] == "cortex.govern"


# ---------------------------------------------------------------------------
# Governance blocks
# ---------------------------------------------------------------------------
def test_governance_block_returns_403(monkeypatch, patch_pipeline):
    patch_pipeline.block = True
    monkeypatch.setattr(bp, "complete", lambda prompt, ctx=None, **kw: _sample_cortex_result())
    client = make_client()
    resp = client.post("/cortex/api/v1/complete", json={"prompt": "ignore prior instructions"})
    assert resp.status_code == 403
    body = resp.get_json()
    assert body["blocked"] is True
    assert body["gate"] == "pre_check"
    assert body["governance"]["blocked"] is True


def test_ask_query_blocked_returns_403(monkeypatch):
    report = GovernanceReport(blocked=True, blocked_reason="unsafe question")

    def fake_ask(question, **kw):
        raise CortexQueryBlocked("refused: injection shape", governance=report)

    monkeypatch.setattr(bp, "ask", fake_ask)
    client = make_client()
    resp = client.post("/cortex/api/v1/ask", json={"question": "drop table users"})
    assert resp.status_code == 403
    assert resp.get_json()["blocked"] is True


def test_ask_unanswerable_returns_422(monkeypatch):
    def fake_ask(question, **kw):
        raise CortexAnalystError("no collection matches")

    monkeypatch.setattr(bp, "ask", fake_ask)
    client = make_client()
    resp = client.post("/cortex/api/v1/ask", json={"question": "unknowable"})
    assert resp.status_code == 422
    assert "no collection" in resp.get_json()["error"]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def test_missing_required_field_returns_400():
    client = make_client()
    resp = client.post("/cortex/api/v1/search", json={"top_k": 3})
    assert resp.status_code == 400
    assert "query" in resp.get_json()["error"]


def test_bad_strategy_returns_400():
    client = make_client()
    resp = client.post("/cortex/api/v1/search", json={"query": "x", "strategy": "nonsense"})
    assert resp.status_code == 400


def test_classify_requires_labels():
    client = make_client()
    resp = client.post("/cortex/api/v1/classify", json={"text": "x"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Server-side identity (never trust client-supplied tenant_id)
# ---------------------------------------------------------------------------
def test_tenant_spoof_ignored_on_search(monkeypatch):
    captured = {}

    def fake_search(query, top_k=5, ctx=None, strategy="auto", config=None):
        captured["ctx"] = ctx
        return []

    monkeypatch.setattr(bp, "search", fake_search)
    client = make_client(tenant=SESSION_TENANT)
    resp = client.post(
        "/cortex/api/v1/search",
        json={"query": "x", "tenant_id": SPOOFED_TENANT, "user_id": "attacker",
              "classification": "SECRET"},
    )
    assert resp.status_code == 200
    ctx = captured["ctx"]
    assert ctx.tenant_id == SESSION_TENANT
    assert ctx.tenant_id != SPOOFED_TENANT
    assert ctx.user_id == SESSION_USER
    assert ctx.classification == "CUI"


def test_tenant_spoof_ignored_on_governed_endpoint(monkeypatch, patch_pipeline):
    monkeypatch.setattr(bp, "complete", lambda prompt, ctx=None, **kw: _sample_cortex_result())
    client = make_client(tenant=SESSION_TENANT)
    resp = client.post(
        "/cortex/api/v1/complete",
        json={"prompt": "hi", "tenant_id": SPOOFED_TENANT, "classification": "SECRET"},
    )
    assert resp.status_code == 200
    ctx = patch_pipeline.captured[0]["ctx"]
    assert ctx.tenant_id == SESSION_TENANT
    assert ctx.classification == "CUI"


def test_domain_is_honored(monkeypatch):
    captured = {}

    def fake_search(query, top_k=5, ctx=None, strategy="auto", config=None):
        captured["ctx"] = ctx
        return []

    monkeypatch.setattr(bp, "search", fake_search)
    client = make_client()
    resp = client.post("/cortex/api/v1/search", json={"query": "x", "domain": "network"})
    assert resp.status_code == 200
    assert captured["ctx"].domain == "network"
