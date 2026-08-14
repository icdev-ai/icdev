# CUI // SP-CTI
r"""``cortex.ask(summarize=True)`` must not reach the cloud tier under air-gap.

The defect (ctx-trust-01), measured on the live tree:

``analyst._llm_summarize`` called ``LLMRouter().invoke("summarization", ...)``.
``summarization`` is declared in ``args/llm_config.yaml`` ONLY under
``task_categories:`` — never under ``routing:``. ``LLMRouter`` resolves a
function as ``routing.get(fn, routing.get("default", {}))``, so an undeclared
name silently takes ``routing.default``, whose chain begins ``kimi-cloud``.

Three consequences, each asserted below:

1. The call was routed by a CLOUD-FIRST chain nobody had chosen for it.
2. No ``exclude_model_ids`` was passed — unlike every other Cortex LLM call —
   so an air-gapped caller still reached for that cloud tier. And because
   ``CORTEX_ROUTING_FUNCTIONS`` omitted the name, ``assert_airgap_ready()``
   never validated it either: the one unguarded call was also the one the
   guard could not see.
3. A failed summary fell through to ``_label_rows_result``, which stamps
   ``confidence_score: 1.0`` and ``grounding="rows_by_construction"`` — a
   STRONGER label than the summarized path earns. A caller who asked for prose
   and silently got none could not tell.

These assert BEHAVIOUR (which function name is resolved, which kwargs reach the
router, what the result metadata says), not that a config key parses.
"""
from __future__ import annotations

import importlib
import pathlib

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture
def analyst():
    return importlib.import_module("tools.cortex.analyst")


@pytest.fixture
def cortex_api():
    return importlib.import_module("tools.cortex.api")


@pytest.fixture
def cortex_config():
    return importlib.import_module("tools.cortex.config")


def _routing() -> dict:
    cfg = yaml.safe_load((REPO_ROOT / "args" / "llm_config.yaml").read_text(encoding="utf-8"))
    return cfg.get("routing") or {}


class _Recorder:
    """Stands in for the router singleton and records how it was invoked."""

    def __init__(self, content="A summary [source: s1]."):
        self.calls: list[tuple] = []
        self._content = content

    def invoke(self, function, request, **kwargs):
        self.calls.append((function, request, kwargs))

        class _Resp:
            content = self._content
            duration_ms = 1
            model = "qwen3-local"

        return _Resp()


# ---------------------------------------------------------------------------
# 1. The routing function is declared, and it is not `summarization`
# ---------------------------------------------------------------------------


def test_the_summarize_function_is_declared_under_routing(cortex_api):
    """An undeclared function silently becomes routing.default — cloud-first."""
    routing = _routing()
    fn = cortex_api.CORTEX_SUMMARIZE_FUNCTION

    assert fn in routing, (
        f"{fn!r} is not under `routing:` in args/llm_config.yaml, so LLMRouter "
        "will fall back to routing.default"
    )
    assert routing[fn].get("chain"), f"{fn!r} declares no chain"


def test_summarization_is_still_not_a_routing_function():
    """Pins the trap itself: `summarization` lives under task_categories only.

    If a later change declares it under `routing:`, this test failing is the
    signal to re-check whether the analyst should use it after all — rather
    than the silent fallback re-appearing unnoticed.
    """
    assert "summarization" not in _routing()


def test_the_summarize_chain_prefers_a_local_model(cortex_api):
    """Cloud-first was the defect; the replacement must not reproduce it."""
    chain = _routing()[cortex_api.CORTEX_SUMMARIZE_FUNCTION]["chain"]
    assert "local" in chain[0], f"chain starts {chain[0]!r}, expected a local model first"


def test_the_airgap_guard_can_see_this_function(cortex_config, cortex_api):
    """assert_airgap_ready() only validates what CORTEX_ROUTING_FUNCTIONS names."""
    assert cortex_api.CORTEX_SUMMARIZE_FUNCTION in cortex_config.CORTEX_ROUTING_FUNCTIONS


def test_every_declared_cortex_routing_function_exists_under_routing(cortex_config):
    routing = _routing()
    missing = [f for f in cortex_config.CORTEX_ROUTING_FUNCTIONS if f not in routing]
    assert not missing, f"declared but not routable: {missing}"


# ---------------------------------------------------------------------------
# 2. The call itself: right function, air-gap exclusions threaded
# ---------------------------------------------------------------------------


def _citations(analyst):
    from tools.cortex.schemas import Citation

    return [Citation(source_id="s1", source_type="analyst", snippet="row one")]


def test_llm_summarize_invokes_the_cortex_function_not_summarization(analyst, monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(importlib.import_module("tools.llm"), "get_router", lambda: rec)

    analyst._llm_summarize("q?", [{"a": 1}], _citations(analyst), None)

    assert rec.calls, "router was never invoked"
    fn = rec.calls[0][0]
    assert fn != "summarization", "still routing through the undeclared name"
    assert fn == importlib.import_module("tools.cortex.api").CORTEX_SUMMARIZE_FUNCTION


def test_airgap_context_threads_exclude_model_ids(analyst, monkeypatch):
    """The kwarg every other Cortex LLM call passes, and this one did not."""
    from tools.cortex.schemas import CortexContext

    rec = _Recorder()
    monkeypatch.setattr(importlib.import_module("tools.llm"), "get_router", lambda: rec)
    monkeypatch.setattr(
        importlib.import_module("tools.cortex.config"),
        "airgap_exclusions",
        lambda ctx=None, config_path=None: ["kimi-cloud", "gpt-4o"],
    )

    analyst._llm_summarize("q?", [{"a": 1}], _citations(analyst), CortexContext())

    _, _, kwargs = rec.calls[0]
    assert "exclude_model_ids" in kwargs, (
        "air-gap exclusions were not passed — an air-gapped caller would still "
        "walk the cloud tier"
    )
    assert "kimi-cloud" in kwargs["exclude_model_ids"]


def test_no_exclusions_kwarg_when_not_airgapped(analyst, monkeypatch):
    """Plain calls stay signature-compatible, matching api._invoke."""
    rec = _Recorder()
    monkeypatch.setattr(importlib.import_module("tools.llm"), "get_router", lambda: rec)
    monkeypatch.setattr(
        importlib.import_module("tools.cortex.config"),
        "airgap_exclusions",
        lambda ctx=None, config_path=None: None,
    )

    analyst._llm_summarize("q?", [{"a": 1}], _citations(analyst), None)

    assert "exclude_model_ids" not in rec.calls[0][2]


def test_it_uses_the_router_singleton_not_a_fresh_llmrouter(analyst):
    """A per-call ``LLMRouter()`` re-parses the config and resets availability.

    Asserted over the parsed AST, not the source text: the fix documents the old
    call in its docstring, and a substring check would match that prose and be
    unable to pass. Only an actual construction call counts.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(analyst._llm_summarize)))
    constructed = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "LLMRouter" not in constructed, "still constructing a router per call"

    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "get_router" in called, "does not go through the router singleton"


# ---------------------------------------------------------------------------
# 3. A failed summary must not be relabelled as maximally trusted
# ---------------------------------------------------------------------------


def test_a_failed_summary_is_recorded_not_silently_upgraded(analyst, monkeypatch):
    from tools.cortex.schemas import CortexResult

    monkeypatch.setattr(analyst, "_llm_summarize", lambda *a, **k: None)
    result = CortexResult(text="raw rows")
    result.data["rows"] = [{"a": 1}]

    out = analyst._finalize_result(result, "q?", True, None)

    assert out.metadata.get("summary_requested") is True
    assert out.metadata.get("summary_unavailable") is True, (
        "a requested summary that never arrived is indistinguishable from one "
        "that was never requested"
    )
    assert out.data.get("summarized") is False


def test_an_unrequested_summary_adds_no_degradation_markers(analyst):
    from tools.cortex.schemas import CortexResult

    result = CortexResult(text="raw rows")
    result.data["rows"] = [{"a": 1}]

    out = analyst._finalize_result(result, "q?", False, None)

    assert "summary_unavailable" not in out.metadata
    assert out.metadata["grounding"] == "rows_by_construction"


def test_a_successful_summary_is_graded_by_the_citation_path(analyst, monkeypatch):
    from tools.cortex.schemas import CortexResult

    monkeypatch.setattr(analyst, "_llm_summarize", lambda *a, **k: "Answer [source: s1].")
    result = CortexResult(text="raw rows")
    result.data["rows"] = [{"a": 1}]
    result.citations = _citations(analyst)

    out = analyst._finalize_result(result, "q?", True, None)

    assert out.data["summarized"] is True
    assert out.metadata["grounding"] == "llm_summary"
    assert "summary_unavailable" not in out.metadata
