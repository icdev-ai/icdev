# CUI // SP-CTI
"""One REST call must run the TRUST chain exactly ONCE (ctx-trust-02).

``rest_v1.py`` imports the GOVERNED facades from ``.api`` — deliberately, so the
REST surface cannot reach the raw ungoverned implementations. Four endpoints
then wrapped those already-governed facades in a SECOND ``GovernancePipeline``
via ``_governed()``:

    api_v1_complete   api_v1_reason   api_v1_classify   api_v1_extract

Each inner facade carries ``@_governed_facade``. So every REST call to one of
them ran two gateway screens, two input-redaction passes, two output-redaction
passes, wrote two ``source_citation_registry`` provenance rows and two
``cortex_audit`` rows, and paid roughly twice the fixed gate latency —
double-counting those operations in ``/cortex/metrics`` as a side effect.

The codebase already knew. ``rest_v1.py`` explains that ``api_v1_agent`` is NOT
wrapped because that "would run the chain twice over one launch and write two
audit rows for it", and names complete/reason/classify/extract as the ones that
do. ``search``/``ask`` are called bare for the same reason. These four were
missed.

Counting pipeline CONSTRUCTIONS rather than audit rows keeps this a pure unit
test — no database, no LLM — while still asserting the thing that actually went
wrong. ``api_v1_govern`` is deliberately excluded: it passes an identity lambda
rather than a facade, so its single pipeline IS the operation.
"""
from __future__ import annotations

import importlib

import pytest
from flask import Flask, g

from tools.cortex.blueprint import cortex_bp
from tools.cortex.schemas import CortexResult


@pytest.fixture(autouse=True)
def _canvas_grant_provisioned(monkeypatch):
    """These tests use a TENANT-SCOPED human, which needs a canvas grant.

    Real principals in this deployment are many human users with no tenant
    (0 of 13 dashboard_users rows carry one) plus service keys, which always
    do. A tenant-scoped HUMAN only appears in a multi-tenant deployment -- and
    there it would be provisioned with a canvas grant, because the canvas guard
    grant-checks exactly that shape.

    The tests below need that shape on purpose: they assert the IDENTITY's
    tenant beats a spoofed tenant in the request body, which is only meaningful
    when the identity has one. So the fixture supplies the provisioning rather
    than the tests dropping the tenant and weakening the assertion to "not the
    spoofed value".

    Scoped to this module and to the grant lookup only. Whether the guard
    ADMITS a principal is covered by tests/security/test_canvas_guard_service_key.py;
    these are REST contract tests and must not fail on provisioning state.
    """
    from tools.security import canvas_access

    monkeypatch.setattr(canvas_access, "check_access", lambda *a, **kw: True)


_API = importlib.import_module("tools.cortex.api")
_REST = importlib.import_module("tools.cortex.rest_v1")


def _client():
    app = Flask(__name__)
    app.register_blueprint(cortex_bp)

    @app.before_request
    def _auth():
        g.current_user = {"id": "u1", "role": "admin", "tenant_id": "t1"}
        g.security_context = {
            "tenant_id": "t1", "user_id": "u1", "classification": "CUI",
        }

    return app.test_client()


class _CountingPipeline:
    """Counts how many times the TRUST chain is entered for one request."""

    constructions: list = []

    def __init__(self, operation="cortex", agent_id="cortex"):
        self.operation = operation
        _CountingPipeline.constructions.append(operation)

    def wrap(self, fn, ctx=None, *, prompt="", context_sources=None, retrieval=True, **kw):
        # **kw because the two call sites pass different keyword sets — the
        # facade decorator adds `attach`, the REST wrapper does not. A fixed
        # signature raises TypeError inside the endpoint's `except Exception`
        # and surfaces as an opaque 500, hiding the count this test is about.
        from tools.cortex.schemas import GovernanceReport

        result = fn(prompt)
        report = GovernanceReport(gates_run=["operation"], outcomes={"operation": "pass"})
        if isinstance(result, CortexResult):
            result.governance = report
        return result, report


class _Response:
    content = '{"label": "a"}'
    # extract() reads structured_output, not content — a fake carrying only
    # content raises AttributeError inside the endpoint's `except Exception`
    # and surfaces as an opaque 500.
    structured_output = {"label": "a"}
    duration_ms = 1
    model = "qwen3-local"
    provider = "ollama"
    input_tokens = 1
    output_tokens = 1
    cost_usd = 0.0


class _FakeRouter:
    """Stands in for the router for every shape ``reason`` can dispatch.

    ``reason`` routes by mode to a multi-step orchestration method rather than
    plain ``invoke`` (_REASON_MODES), so a fake with only ``invoke`` raises
    inside the endpoint's ``except Exception`` and surfaces as an opaque 500 —
    hiding the pipeline count this test exists to assert.
    """

    def invoke(self, function, request, **kwargs):
        return _Response()

    def invoke_chain_of_thought(self, function, request, **kwargs):
        return _Response()

    def invoke_chain_of_debate(self, function, request, **kwargs):
        return _Response()

    def invoke_council(self, function, request, **kwargs):
        return _Response()


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Patch the pipeline in BOTH modules — they are separate references.

    Patching only ``rest_v1.GovernancePipeline`` counts the outer wrapper and
    misses the facade's own, which is precisely the one that made the total two.
    """
    _CountingPipeline.constructions = []
    monkeypatch.setattr(_REST, "GovernancePipeline", _CountingPipeline)
    monkeypatch.setattr(_API, "GovernancePipeline", _CountingPipeline)
    monkeypatch.setattr(_API, "_get_router", lambda: _FakeRouter())
    yield


_CASES = [
    ("complete", "/cortex/api/v1/complete", {"prompt": "draft a note"}),
    ("reason", "/cortex/api/v1/reason", {"prompt": "why?", "mode": "cot"}),
    ("classify", "/cortex/api/v1/classify", {"text": "hello", "labels": ["a", "b"]}),
    (
        # Schema deliberately matches _Response.content so extract's own parse
        # succeeds; an unparseable result raises before the count can be read.
        "extract",
        "/cortex/api/v1/extract",
        {
            "text": "hello",
            "schema": {"type": "object", "properties": {"label": {"type": "string"}}},
        },
    ),
]


@pytest.mark.parametrize("name,path,payload", _CASES)
def test_one_rest_call_enters_the_trust_chain_once(name, path, payload):
    resp = _client().post(path, json=payload)

    assert resp.status_code in (200, 403), (name, resp.status_code, resp.get_data(as_text=True))
    entered = _CountingPipeline.constructions
    assert len(entered) == 1, (
        f"/{name} entered the TRUST chain {len(entered)} times ({entered}) — each "
        f"pass redacts again, writes another provenance row and another "
        f"cortex_audit row, and double-counts the call in /cortex/metrics"
    )


def test_the_govern_endpoint_still_runs_the_chain_once():
    """api_v1_govern is NOT a double-governance case and must not be 'fixed'.

    It passes an identity lambda rather than a facade, so its single pipeline
    IS the operation — that is the endpoint's entire purpose.
    """
    resp = _client().post("/cortex/api/v1/govern", json={"text": "some text"})

    assert resp.status_code in (200, 403), resp.get_data(as_text=True)
    assert len(_CountingPipeline.constructions) == 1


def test_rest_still_imports_the_governed_facades_not_the_raw_impls():
    """The fix must not be 'call the raw implementation instead'.

    Importing ask/search from .analyst/.search_service would make these
    endpoints bypass governance entirely — the opposite defect, and a far worse
    one than running it twice.
    """
    import inspect

    src = inspect.getsource(_REST)
    assert "from .api import" in src
    assert "from .analyst import ask" not in src
    assert "from .search_service import search" not in src
