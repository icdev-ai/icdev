# CUI // SP-CTI
"""One REST call must run the TRUST chain ONCE, not twice (ctx-trust-02).

``rest_v1`` imports the GOVERNED facades from ``.api`` — every one of them is a
``@_governed_facade`` — and then re-wrapped four of them in a SECOND
``GovernancePipeline`` via ``_governed()``::

    api_v1_complete   api_v1_reason   api_v1_classify   api_v1_extract

So one POST ran the chain twice: two gateway screens, two input-redaction
passes, two output-redaction passes, two ``register_citation`` rows and two
``cortex_audit`` rows — roughly double the fixed gate latency, and
``/cortex/metrics`` double-counted every REST-origin call because it aggregates
those audit rows. ``search``/``ask``/``agent`` were already calling their facade
directly; these four were simply missed.

THESE TESTS COUNT REAL ROWS. The failure this task fixes is a *duplicate write*,
and a duplicate write is invisible to a mock — asserting "the pipeline was
invoked" is exactly what let the double wrap ship. So gates 1-6 are faked
(offline: no gateway, no Presidio, no network) while gates 7a/7b are left REAL
and pointed at a temp SQLite database, and every assertion is a SELECT COUNT
taken before and after one HTTP request. Same discipline as
``test_provenance_gate.py``, and for the same reason.

``test_double_wrapping_would_write_two_of_each`` restores the pre-fix shape and
asserts the counts go to 2, so the tests above are proven to discriminate
rather than merely to pass.
"""
from __future__ import annotations

import importlib

import pytest
from flask import Flask, g

import tools.cortex.rest_v1 as rest_v1
from tools.cortex import api, governance
from tools.cortex.blueprint import cortex_bp
from tools.cortex.governance import GovernanceBlockedError, GovernanceReport
from tools.cortex.schemas import CortexContext, CortexResult

registry = importlib.import_module("tools.provenance.registry")
citation_types = importlib.import_module("tools.provenance.citation_types")
cortex_db = importlib.import_module("tools.cortex.db.init_db")

SESSION_TENANT = "tenant-trust02"
SESSION_USER = "user-trust02"

# One case per endpoint the fix touches: (name, url, body, expected response text).
CASES = [
    ("complete", "/cortex/api/v1/complete", {"prompt": "draft a note"}, "llm answer"),
    ("reason", "/cortex/api/v1/reason", {"prompt": "design a cache", "mode": "cot"},
     "llm answer"),
    ("classify", "/cortex/api/v1/classify", {"text": "a crash on boot",
                                             "labels": ["bug", "feature"]}, "bug"),
    ("extract", "/cortex/api/v1/extract", {"text": "n is one",
                                           "schema": {"type": "object"}}, '{"n": 1}'),
]
CASE_IDS = [c[0] for c in CASES]


# ---------------------------------------------------------------------------
# Offline LLM seam
# ---------------------------------------------------------------------------
class _FakeResponse:
    """The accounting surface ``api._result_from_response`` reads."""

    def __init__(self, content: str, structured_output=None):
        self.content = content
        self.structured_output = structured_output
        self.provider = "fake"
        self.model_id = "fake-model"
        self.cost_usd = 0.0
        self.duration_ms = 1
        self.input_tokens = 1
        self.output_tokens = 1
        self.chain_rounds = []
        self.stop_reason = "completed"


class _FakeRouter:
    """Stands in for LLMRouter's multi-step orchestration (``reason``)."""

    def invoke_chain_of_thought(self, function, request):
        return _FakeResponse("llm answer")

    invoke_chain_of_debate = invoke_chain_of_thought
    invoke_council = invoke_chain_of_thought


def _fake_invoke(function, request, context):
    """``api._invoke`` for complete / classify / extract.

    The user turn is the LAST message on the LLMRequest — there is no ``prompt``
    attribute, and reading a missing one would silently answer "llm answer" to
    everything, which ``classify`` would then paper over by degrading to its
    deterministic heuristics and returning the right label for the wrong reason.

    ``classify`` must map the content back onto the caller's labels so the
    endpoint takes its LLM path; ``extract`` is identified by the
    ``output_schema`` only it sets.
    """
    messages = getattr(request, "messages", None) or []
    content = messages[-1].get("content", "") if messages else ""
    if getattr(request, "output_schema", None):
        return _FakeResponse('{"n": 1}', structured_output={"n": 1})
    if "Classify the following text" in content:
        return _FakeResponse("bug")
    return _FakeResponse("llm answer")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def trust_db(tmp_path, monkeypatch):
    """A temp SQLite DB carrying both tables gate 7 writes to.

    ``ICDEV_DB_PATH`` steers ``get_connection()`` (``record_governed_call``) and
    ``registry.DB_PATH`` steers ``register_citation``, whose signature the
    governance gate threads no path through. Both must name the SAME file.
    """
    db_path = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
    monkeypatch.setattr(registry, "DB_PATH", db_path)
    # Module-level latch: a previous test in this process may have set it True
    # against a different database, which would skip DDL on this one.
    monkeypatch.setattr(cortex_db, "_SCHEMA_ENSURED", False)

    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS source_citation_registry ("
            " id TEXT PRIMARY KEY,"
            " citation_type TEXT NOT NULL "
            + citation_types.sqlite_check_clause()
            + ", source_table TEXT NOT NULL,"
            " source_record_id TEXT NOT NULL,"
            " source_doc TEXT,"
            " source_hash TEXT NOT NULL,"
            " anchor_hash TEXT,"
            " merkle_root TEXT,"
            " blockchain_tx_id TEXT,"
            " classification TEXT DEFAULT 'CUI',"
            " project_id TEXT,"
            " trust_score REAL DEFAULT 0.0,"
            " created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.commit()
    finally:
        conn.close()

    cortex_db.init_db()
    return db_path


@pytest.fixture
def offline_chain(monkeypatch, trust_db):
    """Gates 1-6 faked, gates 7a/7b REAL, LLM router replaced.

    Deliberately NOT patched: ``_gate_register_provenance`` and
    ``_gate_record_audit`` — they are the subject of the count.
    """
    monkeypatch.setattr(
        governance, "_gate_check_text",
        lambda text: {"allowed": True, "warnings": [], "blocked_reason": None},
    )
    monkeypatch.setattr(governance, "_gate_redact_input", lambda text, cls: (text, 0))
    monkeypatch.setattr(governance, "_gate_redact_output", lambda text: (text, []))
    monkeypatch.setattr(api, "_invoke", _fake_invoke)
    monkeypatch.setattr(api, "_get_router", lambda: _FakeRouter())
    # The response cache would serve a prior GOVERNED result and audit a hit
    # instead of running the chain — a different row count for a different
    # reason. Force it off so the counts measure only the chain.
    monkeypatch.setattr("tools.cortex.cache.is_enabled", lambda: False)


def make_client():
    """A bare Flask app carrying the cortex blueprint and a synthetic session.

    ``g.current_user`` / ``g.security_context`` are the exact seam the dashboard
    auth middleware fills, so identity is derived server-side exactly as in
    production.

    The guard strip below is not cosmetic. Any test EARLIER IN THE SAME PROCESS
    that does ``from tools.dashboard.app import app`` causes
    ``guard_component_access("cortex", ...)`` to be attached to ``cortex_bp`` —
    and a Flask blueprint replays its deferred ``before_request`` functions on
    EVERY app it is subsequently registered on, including this one. The
    synthetic principal here holds no canvas grant, so the guard aborts 403 and
    every assertion below would measure canvas ACCESS CONTROL instead of the
    governance chain. Dropped on this app only, by qualname, leaving the
    blueprint's own ``_init`` handler in place; canvas access has its own tests.
    """
    app = Flask(__name__)
    app.register_blueprint(cortex_bp)
    app.before_request_funcs["cortex"] = [
        fn for fn in app.before_request_funcs.get("cortex", [])
        if "guard_component_access" not in getattr(fn, "__qualname__", "")
    ]

    @app.before_request
    def _simulate_auth():
        g.current_user = {"id": SESSION_USER, "role": "admin",
                          "tenant_id": SESSION_TENANT}
        g.security_context = {
            "tenant_id": SESSION_TENANT,
            "user_id": SESSION_USER,
            "classification": "CUI",
        }

    return app.test_client()


# ---------------------------------------------------------------------------
# Row counting
# ---------------------------------------------------------------------------
def _count(table: str) -> int:
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM {table}")  # noqa: S608 — fixed literals
        return int(cur.fetchone()[0])
    finally:
        conn.close()


def _audit_functions() -> list:
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT function FROM cortex_audit ORDER BY created_at")
        return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# One POST -> exactly one audit row and one citation row
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name,url,body,expected", CASES, ids=CASE_IDS)
def test_one_post_writes_exactly_one_audit_row(offline_chain, name, url, body, expected):
    client = make_client()

    audit_before = _count("cortex_audit")
    citations_before = _count("source_citation_registry")

    resp = client.post(url, json=body)

    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["text"] == expected

    audit_after = _count("cortex_audit")
    citations_after = _count("source_citation_registry")

    assert audit_after - audit_before == 1, (
        f"POST {url} wrote {audit_after - audit_before} cortex_audit rows; want 1. "
        "Two means the endpoint re-wrapped its already-governed facade in a "
        "second GovernancePipeline — this is ctx-trust-02, and it also "
        "double-counts the call in /cortex/metrics."
    )
    assert citations_after - citations_before == 1, (
        f"POST {url} wrote {citations_after - citations_before} "
        "source_citation_registry rows; want 1."
    )
    # The one row is attributed to this operation, not to some inner alias.
    assert _audit_functions() == [f"cortex.{name}"]


@pytest.mark.parametrize("name,url,body,expected", CASES, ids=CASE_IDS)
def test_one_post_registers_one_provenance_row_joined_to_its_audit_row(
    offline_chain, name, url, body, expected
):
    """The single citation row is the one the single audit row points at.

    Counting each table alone would still pass if the endpoint wrote one row to
    each from DIFFERENT pipeline runs; the provenance_id join is what proves
    they came from the same call.
    """
    from tools.db.storage import get_connection

    client = make_client()
    assert client.post(url, json=body).status_code == 200

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, provenance_id FROM cortex_audit")
        audit_rows = cur.fetchall()
        cur.execute("SELECT id, citation_type FROM source_citation_registry")
        citation_rows = cur.fetchall()
    finally:
        conn.close()

    assert len(audit_rows) == 1
    assert len(citation_rows) == 1
    assert audit_rows[0][1] == citation_rows[0][0], (
        "the audit row's provenance_id does not name the registry row that was "
        "written for the same call"
    )
    assert citation_rows[0][1] == "cortex"


# ---------------------------------------------------------------------------
# Discrimination — the pre-fix shape must fail these tests
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name,url,body,expected", CASES, ids=CASE_IDS)
def test_double_wrapping_would_write_two_of_each(
    offline_chain, monkeypatch, name, url, body, expected
):
    """Restore the pre-ctx-trust-02 shape and assert the counts go to 2.

    Without this, the tests above would pass just as happily against an endpoint
    that never ran the chain at all.
    """
    facade = getattr(rest_v1, name)

    def _double(*args, ctx=None, **kwargs):
        # Exactly what rest_v1 used to do: wrap the ALREADY-GOVERNED facade in a
        # second pipeline, keyed on the same operation.
        return rest_v1._governed(
            f"cortex.{name}",
            str(args[0]) if args else "",
            lambda governed: facade(governed, *args[1:], ctx=ctx, **kwargs),
            ctx,
            retrieval=False,
        )

    monkeypatch.setattr(rest_v1, name, _double)

    client = make_client()
    assert client.post(url, json=body).status_code == 200

    assert _count("cortex_audit") == 2, (
        "the double-wrap reproduction did not write two audit rows — this test "
        "no longer discriminates and the ones above prove nothing"
    )
    assert _count("source_citation_registry") == 2


# ---------------------------------------------------------------------------
# A blocked request still comes back as the 403 governance envelope
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name,url,body,expected", CASES, ids=CASE_IDS)
def test_blocked_pre_check_returns_403_envelope(
    offline_chain, monkeypatch, name, url, body, expected
):
    """A gateway pre-check block inside the facade's own chain -> 403.

    The endpoint no longer runs a pipeline of its own, so the facade's
    ``GovernanceBlockedError`` is the only thing the decorator has to map. Gate 1
    is un-faked here (pointed at a refusing gateway) so the block is raised by
    the REAL chain, not by a stubbed facade.
    """
    monkeypatch.setattr(
        governance, "_gate_check_text",
        lambda text: {"allowed": False, "warnings": [],
                      "blocked_reason": "prompt injection detected"},
    )

    client = make_client()
    resp = client.post(url, json=body)

    assert resp.status_code == 403
    envelope = resp.get_json()
    assert envelope["blocked"] is True
    assert envelope["gate"] == "pre_check"
    assert envelope["error"] == "prompt injection detected"
    assert envelope["governance"]["blocked"] is True
    assert envelope["governance"]["blocked_reason"] == "prompt injection detected"

    # A block is audited once (gate 1 audits on the way out) and never reaches
    # provenance — the operation did not run, so there is no output to attest.
    assert _count("cortex_audit") == 1
    assert _count("source_citation_registry") == 0


def test_blocked_error_from_the_facade_maps_to_403(monkeypatch):
    """The decorator's mapping, isolated from the chain and the database."""
    report = GovernanceReport(
        gates_run=["pre_check"], outcomes={"pre_check": "fail"},
        blocked=True, blocked_reason="refused",
    )

    def _blocking(prompt, ctx=None, **kwargs):
        raise GovernanceBlockedError("pre_check", "refused", report)

    monkeypatch.setattr(rest_v1, "complete", _blocking)
    resp = make_client().post("/cortex/api/v1/complete", json={"prompt": "x"})

    assert resp.status_code == 403
    assert resp.get_json()["governance"]["blocked"] is True


# ---------------------------------------------------------------------------
# Static guard — no endpoint may re-wrap a governed facade again
# ---------------------------------------------------------------------------
def test_governed_facades_are_called_directly_not_rewrapped():
    """``_governed`` may only wrap an operation that is not already governed.

    ``api_v1_govern`` is the sole legitimate caller: its operation is a bare
    ``CortexResult`` echo, not a facade. Every imported facade carries the
    ``__cortex_governed__`` stamp, so this asserts the invariant on the objects
    themselves rather than on the source text.
    """
    for name in ("search", "ask", "complete", "reason", "classify", "extract", "agent"):
        facade = getattr(rest_v1, name)
        assert getattr(facade, "__cortex_governed__", False), (
            f"rest_v1.{name} is not a governed facade — the REST surface would "
            "be reaching a raw, ungoverned implementation"
        )

    source = importlib.import_module("tools.cortex.rest_v1").__file__
    with open(source, encoding="utf-8") as handle:
        body = handle.read()
    # One definition, one call site (in api_v1_govern).
    assert body.count("_governed(") == 2, (
        "_governed() has a new call site. It may only wrap an operation that is "
        "NOT already a _governed_facade; wrapping one runs the TRUST chain twice "
        "(ctx-trust-02)."
    )


def test_context_is_still_derived_server_side(offline_chain, monkeypatch):
    """The fix must not have dropped server-side identity on the way through."""
    captured = {}
    real = rest_v1.complete

    def _spy(prompt, ctx=None, **kwargs):
        captured["ctx"] = ctx
        return real(prompt, ctx=ctx, **kwargs)

    monkeypatch.setattr(rest_v1, "complete", _spy)
    resp = make_client().post(
        "/cortex/api/v1/complete",
        json={"prompt": "hi", "tenant_id": "tenant-evil",
              "classification": "SECRET", "domain": "network"},
    )

    assert resp.status_code == 200
    ctx = captured["ctx"]
    assert isinstance(ctx, CortexContext)
    assert ctx.tenant_id == SESSION_TENANT
    assert ctx.classification == "CUI"
    assert ctx.domain == "network"


def test_result_carries_the_facades_own_governance_report(offline_chain):
    """The 200 envelope still reports the chain that actually ran."""
    resp = make_client().post("/cortex/api/v1/complete", json={"prompt": "hi"})
    assert resp.status_code == 200
    gov = resp.get_json()["governance"]
    assert gov["blocked"] is False
    # The real chain ran: gates 1-3 plus output redaction, provenance and audit.
    assert "pre_check" in gov["gates_run"]
    assert "operation" in gov["gates_run"]
    assert "provenance" in gov["gates_run"]
    assert gov["outcomes"]["provenance"] == "pass"


def test_cortex_result_shape_is_unchanged(offline_chain):
    """Schema stability — callers key off these fields."""
    resp = make_client().post("/cortex/api/v1/classify",
                              json={"text": "a crash", "labels": ["bug", "feature"]})
    assert resp.status_code == 200
    assert set(resp.get_json()) == set(CortexResult(text="x").to_dict())
