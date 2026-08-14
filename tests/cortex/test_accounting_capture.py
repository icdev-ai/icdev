# CUI // SP-CTI
"""Cortex accounting capture is keyed on the CALL, not the return type (ctx-obs-01).

``GovernancePipeline._audit`` used to populate cost/latency/tokens/model ONLY
when the operation happened to return a :class:`CortexResult`. Two facades do
not: ``cortex.search`` returns a ``list[CortexSearchResult]`` and
``cortex.govern`` returns a ``str``. Both appeared in ``calls`` and contributed
nothing to ``cost_usd``, ``avg_latency_ms`` or ``by_model`` — so the single most
expensive facade (backend fan-out + optional CRAG re-retrieval + a rewrite LLM
call) was invisible in the very panel used to decide what to optimise.

What is pinned here:

* ``cortex.search`` records a real, non-zero latency that reaches
  ``/cortex/metrics``'s ``avg_latency_ms``.
* ``cortex.govern`` records latency too — its operation body is the identity
  function, so its honest cost is the gate chain, and ``total_ms`` is what
  measures it.
* Provider spend inside an operation that does not surface it on its return
  value (search's CRAG rewrite) is attributed to that call.
* ``by_function`` counts do NOT change: they come from a SQL ``GROUP BY`` over
  real columns, not from the gates_json detail read, and stay exact even when
  the detail read truncates.
* Every facade that DOES return a CortexResult keeps its own provider-reported
  accounting, byte for byte.

Runs under tests/conftest.py, which forces ICDEV_STORAGE_BACKEND=sqlite.
"""
from __future__ import annotations

import importlib
import json
import time

import pytest

from tools.cortex import governance, metrics, search_service
from tools.cortex.db.init_db import init_db
from tools.cortex.schemas import (
    Citation,
    CortexContext,
    CortexResult,
    CortexSearchResult,
)
from tools.llm.provider import LLMResponse

# A backend "call" this slow is unmistakably non-zero in any latency unit.
SLOW_MS = 8
SLOW_S = SLOW_MS / 1000.0

TEST_CONFIG = {
    "search": {
        "router": {"factual_confidence": 0.75},
        "crag_threshold": 0.0,  # corrective loop off unless a test turns it on
        "timeouts": {"default": 5.0},
        "fan_out": {"backends": ["rag"], "max_workers": 2},
    }
}


@pytest.fixture
def cortex_db(tmp_path, monkeypatch):
    """Point get_connection() at a fresh temp SQLite DB with the Cortex tables."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "cortex.db"))
    init_db()
    return tmp_path


@pytest.fixture
def stub_gates(monkeypatch):
    """Stub every heavy gate seam — but leave the audit write REAL.

    The point of these tests is what lands in ``cortex_audit``, so the one seam
    that must not be faked is the one that writes it.
    """
    monkeypatch.setattr(
        governance, "_gate_check_text",
        lambda text: {"allowed": True, "warnings": [], "blocked_reason": None},
    )
    monkeypatch.setattr(governance, "_gate_redact_input", lambda text, cls: (text, 0))
    monkeypatch.setattr(governance, "_gate_redact_output", lambda text: (text, []))
    monkeypatch.setattr(
        governance, "_gate_validate_citations",
        lambda text, allowed: {"hallucinated_citations": [], "cited_count": 1},
    )
    monkeypatch.setattr(governance, "_gate_find_placeholders", lambda text: [])
    monkeypatch.setattr(
        governance, "_gate_ground_content",
        lambda text, chunks, ctx: {"score": 1.0, "method": "stub", "ungrounded_claims": []},
    )
    monkeypatch.setattr(
        governance, "_gate_register_provenance",
        lambda output_text, ctx, operation, record_id: "scr-obs",
    )


def _audit_rows():
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT function, gates_json FROM cortex_audit ORDER BY created_at")
        return [{"function": f, "gates": json.loads(g)} for f, g in cur.fetchall()]
    finally:
        conn.close()


def _rows_for(function: str):
    return [r for r in _audit_rows() if r["function"] == function]


def _hit(score=0.9, source_id="rag-1", content="a retrieved snippet"):
    return CortexSearchResult(
        content=content, score=score, backend="rag", strategy="native",
        citation=Citation(source_id=source_id),
    )


def _patch_slow_backend(monkeypatch, score=0.9):
    """A ``rag`` adapter that takes real, measurable time to answer."""
    def fake(query, top_k=5, ctx=None):
        time.sleep(SLOW_S)
        return [_hit(score=score, source_id=f"rag-{query}")]

    monkeypatch.setitem(search_service.BACKEND_ADAPTERS, "rag", fake)


class _FakeRouter:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def invoke(self, function, request, **kwargs):
        self.calls.append(function)
        return self.response


def _install_router(monkeypatch, response):
    router = _FakeRouter(response)
    llm_pkg = importlib.import_module("tools.llm")
    monkeypatch.setattr(llm_pkg, "get_router", lambda config_path=None: router)
    return router


# ---------------------------------------------------------------------------
# Acceptance 1 — cortex.search reports non-zero avg_latency_ms
# ---------------------------------------------------------------------------
def test_search_records_non_zero_latency_in_metrics(cortex_db, stub_gates, monkeypatch):
    from tools.cortex import api

    _patch_slow_backend(monkeypatch)
    hits = api.search("who owns the gateway", ctx=CortexContext(tenant_id="t-a"),
                      strategy="rag", config=TEST_CONFIG)
    assert hits, "the stubbed backend returned hits — the facade must pass them through"

    rows = _rows_for("cortex.search")
    assert len(rows) == 1, "one audit row per governed search"
    gates = rows[0]["gates"]
    # The operation itself was timed, not the return type inspected.
    assert gates["operation_ms"] >= SLOW_MS
    assert gates["latency_ms"] >= SLOW_MS
    assert gates["total_ms"] >= gates["operation_ms"]

    # ...and it reaches the panel. Only search rows exist here, so a non-zero
    # average is a non-zero search latency and nothing else.
    out = metrics.summarize(window_hours=24)
    assert out["summary"]["calls"] == 1
    assert out["summary"]["avg_latency_ms"] >= SLOW_MS
    assert [f["function"] for f in out["by_function"]] == ["cortex.search"]


def test_search_latency_was_zero_before_because_the_result_is_a_list(cortex_db, stub_gates, monkeypatch):
    """The regression itself: a list return carries no accounting attributes.

    Reading accounting off the RESULT yields nothing for search; reading it off
    the CALL yields the real numbers. This pins the distinction so a future
    refactor cannot quietly go back to isinstance-gating.
    """
    from tools.cortex import api

    _patch_slow_backend(monkeypatch)
    hits = api.search("q", ctx=CortexContext(tenant_id="t-a"),
                      strategy="rag", config=TEST_CONFIG)

    assert isinstance(hits, list) and not isinstance(hits, CortexResult)
    assert not hasattr(hits, "latency_ms"), "a list has nothing to read accounting from"
    assert _rows_for("cortex.search")[0]["gates"]["latency_ms"] >= SLOW_MS


# ---------------------------------------------------------------------------
# Acceptance 2 — cortex.govern records latency
# ---------------------------------------------------------------------------
def test_govern_records_latency(cortex_db, stub_gates, monkeypatch):
    """govern's body is the identity function; its real cost IS the gate chain."""
    from tools.cortex import api

    # Make one gate demonstrably slow so the recorded figure is provably the
    # gate chain's time and not an artefact of measurement noise.
    real_redact = governance._gate_redact_output

    def slow_redact(text):
        time.sleep(SLOW_S)
        return real_redact(text)

    monkeypatch.setattr(governance, "_gate_redact_output", slow_redact)

    report = api.govern("a drafted answer [source: s1]", sources=["s1"],
                        ctx=CortexContext(tenant_id="t-a"))
    assert not report.blocked

    gates = _rows_for("cortex.govern")[0]["gates"]
    # The identity body is genuinely near-instant — that is exactly why keying
    # accounting on the operation alone would still have recorded ~nothing.
    assert gates["total_ms"] >= SLOW_MS
    assert gates["latency_ms"] >= SLOW_MS
    assert metrics.summarize(window_hours=24)["summary"]["avg_latency_ms"] >= SLOW_MS


# ---------------------------------------------------------------------------
# Acceptance 3 — by_function counts unchanged (no regression)
# ---------------------------------------------------------------------------
def test_by_function_counts_are_exact_and_unaffected_by_accounting(cortex_db, stub_gates, monkeypatch):
    """Counts come from the SQL GROUP BY over real columns, not from gates_json.

    Adding accounting to the blob cannot move them — and they stay exact even
    when the gates_json detail read truncates to a single row.
    """
    from tools.cortex import api
    from tools.cortex.governance import GovernancePipeline

    _patch_slow_backend(monkeypatch)
    ctx = CortexContext(tenant_id="t-a")
    api.search("first query", ctx=ctx, strategy="rag", config=TEST_CONFIG)
    api.search("second query", ctx=ctx, strategy="rag", config=TEST_CONFIG)
    api.govern("drafted [source: s1]", sources=["s1"], ctx=ctx)
    for _ in range(3):
        GovernancePipeline(operation="cortex.complete").wrap(
            lambda p: CortexResult(text="ok", latency_ms=42, cost=0.5,
                                   model="m", input_tokens=7, output_tokens=3),
            ctx, prompt="q", retrieval=False,
        )

    expected = {"cortex.search": 2, "cortex.govern": 1, "cortex.complete": 3}

    out = metrics.summarize(window_hours=24, use_memo=False)
    assert {f["function"]: f["calls"] for f in out["by_function"]} == expected
    assert out["summary"]["calls"] == 6
    assert out["by_outcome"] == {"pass": 6}

    # The counts are SQL-derived: cap the detail read at one row and they hold.
    capped = metrics.summarize(window_hours=24, use_memo=False, detail_limit=1)
    assert {f["function"]: f["calls"] for f in capped["by_function"]} == expected
    assert capped["summary"]["calls"] == 6
    assert capped["detail"]["truncated"] is True


def test_cortex_result_accounting_is_unchanged(cortex_db, stub_gates):
    """Facades returning a CortexResult keep their provider-reported figures."""
    from tools.cortex.governance import GovernancePipeline

    GovernancePipeline(operation="cortex.complete").wrap(
        lambda p: CortexResult(text="ok", provider="ollama", model="qwen",
                               cost=0.25, latency_ms=1234,
                               input_tokens=300, output_tokens=120),
        CortexContext(tenant_id="t-a"), prompt="q", retrieval=False,
    )

    gates = _rows_for("cortex.complete")[0]["gates"]
    assert gates["latency_ms"] == 1234  # NOT overwritten by the pipeline's clock
    assert gates["cost_usd"] == 0.25
    assert gates["provider"] == "ollama" and gates["model"] == "qwen"
    assert gates["input_tokens"] == 300 and gates["output_tokens"] == 120
    # The pipeline's own measurements are recorded alongside, never instead.
    assert gates["operation_ms"] >= 0.0 and gates["total_ms"] > 0.0


# ---------------------------------------------------------------------------
# Provider spend inside a non-CortexResult operation
# ---------------------------------------------------------------------------
def test_search_crag_rewrite_cost_is_attributed_to_the_search_call(cortex_db, stub_gates, monkeypatch):
    """The rewrite LLM call is search's real spend — it must not vanish."""
    from tools.cortex import api

    # Low-confidence hits under a live threshold trigger exactly one rewrite.
    _patch_slow_backend(monkeypatch, score=0.2)
    config = json.loads(json.dumps(TEST_CONFIG))
    config["search"]["crag_threshold"] = 0.5
    router = _install_router(monkeypatch, LLMResponse(
        content="a materially different rewritten query",
        provider="ollama", model_id="qwen", cost_usd=0.004,
        input_tokens=90, output_tokens=12,
    ))

    api.search("vague question", ctx=CortexContext(tenant_id="t-a"),
               strategy="rag", config=config)
    assert "cortex_search_rewrite" in router.calls, "the corrective pass must have run"

    gates = _rows_for("cortex.search")[0]["gates"]
    assert gates["cost_usd"] == pytest.approx(0.004)
    assert gates["input_tokens"] == 90 and gates["output_tokens"] == 12
    assert gates["model"] == "qwen" and gates["provider"] == "ollama"

    out = metrics.summarize(window_hours=24)
    assert out["summary"]["cost_usd"] == pytest.approx(0.004)
    assert [m["model"] for m in out["by_model"]] == ["qwen"]


def test_record_llm_call_is_a_noop_outside_a_governed_operation():
    """Ungoverned callers have nothing to attribute spend to — and must not raise."""
    assert governance._llm_accounting.get() is None
    governance.record_llm_call(LLMResponse(cost_usd=1.0, input_tokens=10))
    assert governance._llm_accounting.get() is None


def test_record_llm_call_survives_a_garbage_response():
    tally = governance._new_accounting()
    token = governance._llm_accounting.set(tally)
    try:
        governance.record_llm_call(object())  # no accounting attributes at all
        governance.record_llm_call(None)
    finally:
        governance._llm_accounting.reset(token)
    # Best-effort: nothing usable was found, nothing blew up.
    assert tally["cost_usd"] == 0.0


# ---------------------------------------------------------------------------
# _accounting_fields precedence, in isolation
# ---------------------------------------------------------------------------
def _report(operation_ms=0.0, total_ms=0.0, tally=None):
    """A GovernanceReport carrying the timing + tally _accounting_fields reads.

    ctx-obs-02 landed total_ms/operation_ms on the report before this work
    merged, so the re-applied capture consumes them instead of measuring its own
    `pipeline_ms`. `total_ms` is the whole governed call - the figure that was
    called `pipeline_ms` on the original branch.
    """
    from tools.cortex.schemas import GovernanceReport

    r = GovernanceReport()
    r.operation_ms = operation_ms
    r.total_ms = total_ms
    r.llm_tally = tally or {}
    return r


def test_accounting_prefers_the_results_own_provider_latency():
    out = governance._accounting_fields(
        CortexResult(latency_ms=500, cost=0.1, model="m", provider="p",
                     input_tokens=5, output_tokens=2),
        _report(operation_ms=12.0, total_ms=60.0, tally={
            "calls": 1, "cost_usd": 9.9, "input_tokens": 1, "output_tokens": 1,
            "provider": "other", "model": "other"}),
    )
    assert out["latency_ms"] == 500.0
    assert out["cost_usd"] == 0.1          # tally must not overwrite a reported figure
    assert out["model"] == "m" and out["provider"] == "p"


def test_accounting_falls_back_to_pipeline_time_for_a_list_result():
    out = governance._accounting_fields([_hit()], _report(operation_ms=37.5, total_ms=50.0))
    assert out["latency_ms"] == 50.0
    assert out["cost_usd"] == 0.0


def test_latency_fallback_is_pipeline_time_not_operation_time():
    """govern's shape: an identity body, so only the gate chain took any time.

    Falling back to operation_ms would report ~5 microseconds for a call that
    really took 50ms of gates — a more precise-looking lie than the zero it
    replaced. The fallback is deliberately report.total_ms, the whole-call
    measurement ctx-obs-02 already provides.
    """
    report = _report(operation_ms=0.005, total_ms=50.0)
    out = governance._accounting_fields("some text", report)
    assert out["latency_ms"] == 50.0
    assert out["latency_ms"] != report.operation_ms


def test_accounting_uses_the_tally_when_the_result_reports_nothing():
    out = governance._accounting_fields("some text", _report(
        operation_ms=5.0, total_ms=22.0,
        tally={"calls": 2, "cost_usd": 0.02, "input_tokens": 40, "output_tokens": 8,
               "provider": "ollama", "model": "qwen"}))
    assert out["cost_usd"] == 0.02
    assert out["input_tokens"] == 40 and out["output_tokens"] == 8
    assert out["model"] == "qwen"
    assert out["latency_ms"] == 22.0


def test_accounting_tolerates_missing_timing_and_tally():
    out = governance._accounting_fields(None, _report())
    assert out["latency_ms"] == 0.0
    assert out["cost_usd"] == 0.0


# ---------------------------------------------------------------------------
# Nesting / isolation of the ContextVar tally
# ---------------------------------------------------------------------------
def test_nested_governed_calls_keep_separate_tallies(cortex_db, stub_gates):
    """An inner governed call must not drain the outer call's tally."""
    from tools.cortex.governance import GovernancePipeline

    ctx = CortexContext(tenant_id="t-a")

    def outer(prompt):
        governance.record_llm_call(LLMResponse(cost_usd=0.1, model_id="outer-model",
                                               input_tokens=10, output_tokens=1))
        GovernancePipeline(operation="cortex.inner").wrap(
            lambda p: governance.record_llm_call(
                LLMResponse(cost_usd=0.7, model_id="inner-model",
                            input_tokens=70, output_tokens=7)
            ) or "inner text",
            ctx, prompt="inner", retrieval=False,
        )
        return "outer text"

    GovernancePipeline(operation="cortex.outer").wrap(
        outer, ctx, prompt="q", retrieval=False,
    )

    assert _rows_for("cortex.inner")[0]["gates"]["cost_usd"] == pytest.approx(0.7)
    assert _rows_for("cortex.outer")[0]["gates"]["cost_usd"] == pytest.approx(0.1)
    assert _rows_for("cortex.outer")[0]["gates"]["model"] == "outer-model"


# ---------------------------------------------------------------------------
# The acceptance criterion at the route the operator actually reads
# ---------------------------------------------------------------------------
@pytest.fixture
def metrics_client(icdev_db, monkeypatch):
    """Dashboard app with the cortex blueprint, pointed at conftest's temp DB."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))
    import tools.dashboard.auth as _auth

    monkeypatch.setattr(_auth, "DB_PATH", str(icdev_db))
    init_db()

    from tools.cortex.blueprint import cortex_bp
    from tools.dashboard.app import app

    if "cortex" not in app.blueprints:
        app.register_blueprint(cortex_bp)
    app.config["TESTING"] = True
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = "test-admin"
        yield client


def test_cortex_metrics_route_reports_search_latency(metrics_client, stub_gates, monkeypatch):
    """/cortex/metrics — the panel itself, not just the aggregation function."""
    from tools.cortex import api

    _patch_slow_backend(monkeypatch)
    api.search("a real search", ctx=CortexContext(tenant_id="default"),
               strategy="rag", config=TEST_CONFIG)
    metrics.reset_memo()

    payload = metrics_client.get("/cortex/api/metrics").get_json()
    assert payload["summary"]["avg_latency_ms"] >= SLOW_MS
    assert any(f["function"] == "cortex.search" for f in payload["by_function"])
    assert metrics_client.get("/cortex/metrics").status_code == 200


def test_a_failing_operation_still_records_its_latency(cortex_db, stub_gates):
    """A provider failure costs time (and often money); the row must say so."""
    from tools.cortex.governance import GovernancePipeline

    def boom(prompt):
        time.sleep(SLOW_S)
        raise RuntimeError("provider exploded")

    with pytest.raises(RuntimeError):
        GovernancePipeline(operation="cortex.complete").wrap(
            boom, CortexContext(tenant_id="t-a"), prompt="q", retrieval=False,
        )

    gates = _rows_for("cortex.complete")[0]["gates"]
    assert gates["operation_ms"] >= SLOW_MS
    assert gates["latency_ms"] >= SLOW_MS
