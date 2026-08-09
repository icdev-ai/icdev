# CUI // SP-CTI
"""Unit tests for tools/cortex/validators.py (ctx-expose-02).

The validators are the single request-validation source shared by the REST v1
blueprint and the MCP tool family. These tests pin the coercion/bounds/enum
rules and the rule that identity fields are never read (only ``domain`` is
surfaced from the payload).
"""
from __future__ import annotations

import pytest

from tools.cortex import validators as v


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------
def test_validate_search_defaults():
    out = v.validate_search({"query": "hello"})
    assert out == {"query": "hello", "top_k": 5, "strategy": "auto"}


def test_validate_search_requires_query():
    with pytest.raises(v.CortexValidationError):
        v.validate_search({"top_k": 3})


def test_validate_search_rejects_unknown_strategy():
    with pytest.raises(v.CortexValidationError):
        v.validate_search({"query": "x", "strategy": "telepathy"})


@pytest.mark.parametrize("bad", [0, 51, -1, "5", True])
def test_validate_search_top_k_bounds(bad):
    with pytest.raises(v.CortexValidationError):
        v.validate_search({"query": "x", "top_k": bad})


def test_validate_search_strategy_case_insensitive():
    assert v.validate_search({"query": "x", "strategy": "RAG"})["strategy"] == "rag"


# ---------------------------------------------------------------------------
# ask
# ---------------------------------------------------------------------------
def test_validate_ask_defaults():
    out = v.validate_ask({"question": "how many?"})
    assert out["mode"] == "auto"
    assert out["canvas"] is None
    assert out["collections"] is None
    assert out["summarize"] is False


def test_validate_ask_bad_mode():
    with pytest.raises(v.CortexValidationError):
        v.validate_ask({"question": "x", "mode": "psychic"})


def test_validate_ask_collections_must_be_strings():
    with pytest.raises(v.CortexValidationError):
        v.validate_ask({"question": "x", "collections": [1, 2]})


# ---------------------------------------------------------------------------
# complete
# ---------------------------------------------------------------------------
def test_validate_complete_minimal():
    out = v.validate_complete({"prompt": "draft"})
    assert out == {"prompt": "draft", "system_prompt": ""}


def test_validate_complete_optional_numerics():
    out = v.validate_complete({"prompt": "p", "max_tokens": 100, "temperature": 0.5})
    assert out["max_tokens"] == 100
    assert out["temperature"] == 0.5


def test_validate_complete_temperature_out_of_range():
    with pytest.raises(v.CortexValidationError):
        v.validate_complete({"prompt": "p", "temperature": 5})


def test_validate_complete_temperature_bool_rejected():
    with pytest.raises(v.CortexValidationError):
        v.validate_complete({"prompt": "p", "temperature": True})


# ---------------------------------------------------------------------------
# classify / extract
# ---------------------------------------------------------------------------
def test_validate_classify_ok():
    out = v.validate_classify({"text": "a crash", "labels": ["bug", "feat"]})
    assert out == {"text": "a crash", "labels": ["bug", "feat"]}


def test_validate_classify_requires_nonempty_labels():
    with pytest.raises(v.CortexValidationError):
        v.validate_classify({"text": "x", "labels": []})


def test_validate_extract_requires_schema_object():
    with pytest.raises(v.CortexValidationError):
        v.validate_extract({"text": "x", "schema": []})
    out = v.validate_extract({"text": "x", "schema": {"type": "object"}})
    assert out["schema"] == {"type": "object"}


# ---------------------------------------------------------------------------
# govern
# ---------------------------------------------------------------------------
def test_validate_govern_defaults():
    out = v.validate_govern({"text": "candidate"})
    assert out["retrieval"] is True
    assert out["operation"] == "cortex.govern"
    assert out["context_sources"] is None


def test_validate_govern_context_sources_type():
    with pytest.raises(v.CortexValidationError):
        v.validate_govern({"text": "x", "context_sources": "not-a-list"})
    assert v.validate_govern({"text": "x", "context_sources": 3})["context_sources"] == 3


# ---------------------------------------------------------------------------
# agent (hgx-cx-02) — the one operation that makes the platform ACT
# ---------------------------------------------------------------------------
def test_validate_agent_defaults():
    out = v.validate_agent({"goal": "ship it"})
    assert out == {"goal": "ship it", "mode": "auto", "roles": None,
                   "graph": None, "max_iterations": 12}


def test_validate_agent_rejects_unknown_mode():
    with pytest.raises(v.CortexValidationError):
        v.validate_agent({"goal": "x", "mode": "yolo"})


def test_validate_agent_graph_requires_a_workflow_id():
    """A graph run NAMES a workflow — it can never be inferred."""
    with pytest.raises(v.CortexValidationError):
        v.validate_agent({"goal": "x", "mode": "graph"})
    with pytest.raises(v.CortexValidationError):
        v.validate_agent({"goal": "x", "mode": "graph", "graph": {}})


def test_validate_agent_graph_ids_are_slugs_not_paths():
    for bad in ("../../etc/passwd", "a/b", "has space", ""):
        with pytest.raises(v.CortexValidationError):
            v.validate_agent({"goal": "x", "mode": "graph",
                              "graph": {"workflow_id": bad}})


def test_validate_agent_graph_inputs_keys_are_bounded():
    ok = v.validate_agent({"goal": "x", "mode": "graph", "graph": {
        "workflow_id": "full_sdlc", "inputs": {"repo": "icdev"}}})
    assert ok["graph"]["inputs"] == {"repo": "icdev"}
    with pytest.raises(v.CortexValidationError):
        v.validate_agent({"goal": "x", "mode": "graph", "graph": {
            "workflow_id": "full_sdlc", "inputs": {"../evil": 1}}})


def test_validate_agent_bounds_iterations():
    with pytest.raises(v.CortexValidationError):
        v.validate_agent({"goal": "x", "max_iterations": 999})
    with pytest.raises(v.CortexValidationError):
        v.validate_agent({"goal": "x", "max_iterations": 0})
    assert v.validate_agent({"goal": "x", "max_iterations": 3})["max_iterations"] == 3


def test_validate_agent_llm_function_is_never_a_model_id():
    """LLM-agnostic: the caller names a routing chain in args/llm_config.yaml."""
    for bad in ("claude-opus-4-20250514", "gpt-4o", "Llama-3", "a b"):
        with pytest.raises(v.CortexValidationError):
            v.validate_agent({"goal": "x", "llm_function": bad})
    assert v.validate_agent(
        {"goal": "x", "llm_function": "code_generation"})["llm_function"] == "code_generation"


def test_validate_agent_never_surfaces_privilege_fields():
    """tools / tool_handlers / rubric / webhook_url must not survive validation.

    A caller that could name its agent's tools would be choosing its own
    privileges; a caller-supplied webhook_url is an SSRF primitive.
    """
    out = v.validate_agent({
        "goal": "x",
        "tools": [{"name": "bash"}],
        "tool_handlers": {"bash": "os.system"},
        "rubric": True,
        "webhook_url": "http://169.254.169.254/",
        "tenant_id": "evil", "user_id": "root", "classification": "UNCLASSIFIED",
    })
    for key in ("tools", "tool_handlers", "rubric", "webhook_url",
                "tenant_id", "user_id", "classification"):
        assert key not in out


def test_validate_agent_roles_are_slugs():
    assert v.validate_agent(
        {"goal": "x", "roles": ["ai_developer"]})["roles"] == ["ai_developer"]
    with pytest.raises(v.CortexValidationError):
        v.validate_agent({"goal": "x", "roles": ["../../etc/passwd"]})
    with pytest.raises(v.CortexValidationError):
        v.validate_agent({"goal": "x", "roles": [f"r{i}" for i in range(21)]})


# ---------------------------------------------------------------------------
# domain — the one honored client-supplied context field
# ---------------------------------------------------------------------------
def test_domain_of_reads_only_domain():
    assert v.domain_of({"domain": "network", "tenant_id": "evil"}) == "network"
    assert v.domain_of({}) == ""


def test_non_dict_body_rejected():
    for fn in (v.validate_search, v.validate_ask, v.validate_complete,
               v.validate_classify, v.validate_extract, v.validate_govern,
               v.validate_agent):
        with pytest.raises(v.CortexValidationError):
            fn(None)
        with pytest.raises(v.CortexValidationError):
            fn("a string")
