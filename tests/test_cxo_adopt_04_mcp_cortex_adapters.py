# CUI // SP-CTI
"""cxo-adopt-04 — the three dead MCP tools are retargeted at Cortex.

Before this change all three were non-functional:

  * ``cot_invoke`` / ``cod_invoke`` pointed at ``tools.llm.chain_orchestrator``
    module-level functions ``invoke_chain_of_thought`` /
    ``invoke_chain_of_debate`` that DO NOT EXIST (they are ChainOrchestrator
    methods), so ``unified_server._resolve_handler`` installed an error stub.
  * ``nlq_query`` resolved fine but never invoked an LLM — it listed tables and
    told the caller to use the dashboard.

They now route through the governed Cortex facade. The MCP tool names and
input_schemas are a public contract and must be byte-identical to what shipped;
the registry-contract tests below pin them.

Runs under conftest, which forces ICDEV_STORAGE_BACKEND=sqlite.
"""
from __future__ import annotations

import importlib

import pytest

import tools.mcp.gap_handlers as gh
from tools.cortex.db.init_db import init_db
from tools.cortex.schemas import CortexResult

cortex_pkg = importlib.import_module("tools.cortex")
cortex_api = importlib.import_module("tools.cortex.api")


def _entry(name: str) -> dict:
    """Look up a tool across both registries.

    cot_invoke / cod_invoke live in RESOURCE_REGISTRY as "tool-overflow"
    entries (unified_server registers any entry carrying an input_schema as a
    tool); nlq_query lives in TOOL_REGISTRY.
    """
    from tools.mcp.tool_registry import RESOURCE_REGISTRY, TOOL_REGISTRY

    return {**TOOL_REGISTRY, **RESOURCE_REGISTRY}[name]


class _FakeResponse:
    provider = "chain_orchestrator"
    model_id = "test-model"
    cost_usd = 0.0
    duration_ms = 7
    input_tokens = 3
    output_tokens = 5

    def __init__(self, content: str):
        self.content = content


class _FakeRouter:
    """Stands in for LLMRouter at the api._get_router seam."""

    def __init__(self):
        self.calls = []

    def invoke_chain_of_thought(self, function, request):
        self.calls.append(("cot", function))
        return _FakeResponse("stepwise answer")

    def invoke_chain_of_debate(self, function, request):
        self.calls.append(("debate", function))
        return _FakeResponse("debated answer")


@pytest.fixture
def cortex_db(tmp_path, monkeypatch):
    """Point get_connection() at a fresh temp SQLite DB with the Cortex tables."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "cortex.db"))
    init_db()
    return tmp_path / "cortex.db"


def _audit_rows():
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT function, outcome FROM cortex_audit ORDER BY created_at")
        return [{"function": r[0], "outcome": r[1]} for r in cur.fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Registry contract — handlers resolve (no stub) and schemas are unchanged
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "tool,handler",
    [
        ("cot_invoke", "handle_cot_invoke"),
        ("cod_invoke", "handle_cod_invoke"),
        ("nlq_query", "handle_nlq_query"),
    ],
)
def test_handler_resolves_to_a_real_callable(tool, handler):
    """The regression that made these dead: getattr(module, handler) raised."""
    entry = _entry(tool)
    assert entry["module"] == "tools.mcp.gap_handlers"
    assert entry["handler"] == handler
    mod = importlib.import_module(entry["module"])
    assert callable(getattr(mod, handler, None)), f"{tool} would resolve to an error stub"


@pytest.mark.parametrize(
    "tool,properties,required",
    [
        (
            "cot_invoke",
            {"function", "prompt", "system_prompt", "max_rounds", "self_consistency_runs"},
            ["function", "prompt"],
        ),
        (
            "cod_invoke",
            {"function", "prompt", "system_prompt", "num_debaters", "debate_rounds"},
            ["function", "prompt"],
        ),
        ("nlq_query", {"query", "project_id"}, ["query"]),
    ],
)
def test_input_schema_is_unchanged(tool, properties, required):
    """MCP names + input_schemas are a public contract — retargeting must not move them."""
    schema = _entry(tool)["input_schema"]
    assert set(schema["properties"]) == properties
    assert schema.get("required") == required


@pytest.mark.parametrize("tool", ["cot_invoke", "cod_invoke", "nlq_query"])
def test_description_documents_the_ignored_params(tool):
    assert "IGNORED" in _entry(tool)["description"]


def test_generate_registry_nlq_entry_matches_the_live_registry():
    """generate_registry.py rewrites tool_registry.py — drift here silently reverts the fix.

    (cot_invoke / cod_invoke are hand-maintained in tool_registry.py and have no
    generate_registry source, so only nlq_query is comparable.)
    """
    gen = importlib.import_module("tools.mcp.generate_registry")
    source = next(
        v
        for v in vars(gen).values()
        if isinstance(v, dict) and "nlq_query" in v and isinstance(v["nlq_query"], dict)
    )["nlq_query"]
    assert source == _entry("nlq_query")


# ---------------------------------------------------------------------------
# cot_invoke / cod_invoke -> cortex.reason(mode=...)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "handler,mode",
    [(gh.handle_cot_invoke, "cot"), (gh.handle_cod_invoke, "debate")],
)
def test_reason_adapter_calls_the_governed_facade(handler, mode, monkeypatch):
    seen = {}

    def _reason(prompt, mode="cot", function="", ctx=None, **kw):
        seen.update(prompt=prompt, mode=mode, function=function,
                    system_prompt=kw.get("system_prompt"))
        return CortexResult(text="governed reasoning")

    monkeypatch.setattr(cortex_pkg, "reason", _reason)

    out = handler({"function": "code_generation", "prompt": "why?", "system_prompt": "be terse"})

    assert seen["mode"] == mode
    assert seen["function"] == "code_generation"
    assert seen["prompt"] == "why?"
    assert seen["system_prompt"] == "be terse"
    assert out["text"] == "governed reasoning"
    assert out["content"] == "governed reasoning"  # legacy field name kept
    assert out["classification"] == "CUI // SP-CTI"
    assert "error" not in out


@pytest.mark.parametrize(
    "handler,args,expected",
    [
        (
            gh.handle_cot_invoke,
            {"max_rounds": 5, "self_consistency_runs": 3},
            ["max_rounds", "self_consistency_runs"],
        ),
        (
            gh.handle_cod_invoke,
            {"num_debaters": 5, "debate_rounds": 4},
            ["num_debaters", "debate_rounds"],
        ),
    ],
)
def test_unsupported_params_are_accepted_and_echoed_as_ignored(
    handler, args, expected, monkeypatch
):
    """The facade has no seam for these; the no-op must be visible, not silent."""
    monkeypatch.setattr(cortex_pkg, "reason", lambda *a, **kw: CortexResult(text="ok"))
    out = handler({"function": "default", "prompt": "p", **args})
    assert out["ignored_params"] == expected
    assert "error" not in out


@pytest.mark.parametrize("handler", [gh.handle_cot_invoke, gh.handle_cod_invoke])
def test_reason_adapter_requires_a_prompt(handler):
    assert handler({"function": "default"})["error"] == "prompt is required"


@pytest.mark.parametrize("handler", [gh.handle_cot_invoke, gh.handle_cod_invoke])
def test_reason_adapter_reports_facade_errors(handler, monkeypatch):
    def _boom(*a, **kw):
        raise RuntimeError("router exploded")

    monkeypatch.setattr(cortex_pkg, "reason", _boom)
    assert handler({"function": "default", "prompt": "p"})["error"] == "router exploded"


@pytest.mark.parametrize(
    "handler,mode",
    [(gh.handle_cot_invoke, "cot"), (gh.handle_cod_invoke, "debate")],
)
def test_reason_adapter_writes_a_cortex_audit_row(handler, mode, cortex_db, monkeypatch):
    """End to end through the REAL governed facade — only the router is stubbed."""
    router = _FakeRouter()
    monkeypatch.setattr(cortex_api, "_get_router", lambda: router)

    out = handler({"function": "default", "prompt": "explain the tradeoff"})

    assert "error" not in out
    assert out["metadata"]["reason_mode"] == mode
    assert router.calls == [(mode, "default")]

    rows = _audit_rows()
    assert [r["function"] for r in rows] == ["cortex.reason"]
    assert rows[0]["outcome"] in {"pass", "warn"}


# ---------------------------------------------------------------------------
# nlq_query -> cortex.ask(mode='nlq')
# ---------------------------------------------------------------------------
def test_nlq_query_calls_the_analyst_in_nlq_mode(monkeypatch):
    seen = {}

    def _ask(question, mode="auto", ctx=None, **kw):
        seen.update(question=question, mode=mode)
        return CortexResult(text="3 rows from cortex_audit", data={"row_count": 3})

    monkeypatch.setattr(cortex_pkg, "ask", _ask)

    out = gh.handle_nlq_query({"query": "how many audit rows"})

    assert seen == {"question": "how many audit rows", "mode": "nlq"}
    assert out["status"] == "ok"
    assert out["query"] == "how many audit rows"
    assert out["text"] == "3 rows from cortex_audit"
    assert out["data"]["row_count"] == 3
    # The pre-Cortex stub answered with this instead of running a query.
    assert "require the dashboard" not in str(out)


def test_nlq_query_echoes_project_id_as_ignored(monkeypatch):
    monkeypatch.setattr(cortex_pkg, "ask", lambda *a, **kw: CortexResult(text="ok"))
    out = gh.handle_nlq_query({"query": "q", "project_id": "icdev"})
    assert out["ignored_params"] == ["project_id"]


def test_nlq_query_requires_a_query():
    assert gh.handle_nlq_query({})["error"] == "query is required"


def test_nlq_query_surfaces_a_blocked_query(monkeypatch):
    from tools.cortex.analyst import CortexQueryBlocked

    def _blocked(*a, **kw):
        raise CortexQueryBlocked("DROP TABLE is not a SELECT")

    monkeypatch.setattr(cortex_pkg, "ask", _blocked)
    out = gh.handle_nlq_query({"query": "drop everything"})
    assert out["status"] == "blocked"
    assert out["blocked"] is True
    assert "not a SELECT" in out["error"]


def test_nlq_query_writes_a_cortex_audit_row(cortex_db, monkeypatch):
    """End to end through the REAL governed facade — only the NL->SQL impl is stubbed."""
    monkeypatch.setattr(
        cortex_api,
        "_ask_impl",
        lambda question, mode="auto", ctx=None, **kw: CortexResult(
            text="1 row", data={"rows": [{"n": 1}], "sql": "SELECT 1", "mode": mode}
        ),
    )
    # ``ask`` captured _ask_impl at decoration time; rebuild the governed wrapper
    # around the stub so the pipeline (and its audit write) still runs for real.
    governed = cortex_api._governed_facade(
        "cortex.ask", text_param="question", retrieval=False, attach=False
    )(cortex_api._ask_impl)
    monkeypatch.setattr(cortex_pkg, "ask", governed)

    out = gh.handle_nlq_query({"query": "how many rows in cortex_audit"})

    assert out["status"] == "ok"
    assert out["data"]["mode"] == "nlq"

    rows = _audit_rows()
    assert [r["function"] for r in rows] == ["cortex.ask"]
