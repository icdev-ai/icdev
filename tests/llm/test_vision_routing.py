# CUI // SP-CTI
"""Multimodal / vision routing for AI assist (incl. Ollama.com multimodal).

Verifies:
  - request.images materialize into message image blocks
  - the router detects image-bearing requests
  - the chain is reordered so vision-capable models lead (Ollama.com cloud
    minimax-m3 is vision-capable and gets promoted)
"""
from __future__ import annotations

import pytest

from tools.llm.provider import (
    LLMRequest, attach_images_to_messages, messages_have_images,
)


def test_attach_images_appends_block_to_last_user_message():
    msgs = [{"role": "user", "content": "Describe this chart"}]
    out = attach_images_to_messages(msgs, ["AAAA"])
    assert isinstance(out[0]["content"], list)
    kinds = [b.get("type") for b in out[0]["content"]]
    assert "text" in kinds and "image" in kinds
    # original not mutated
    assert msgs[0]["content"] == "Describe this chart"


def test_attach_images_handles_data_uri():
    out = attach_images_to_messages([{"role": "user", "content": ""}],
                                    ["data:image/jpeg;base64,QUJD"])
    block = [b for b in out[0]["content"] if b["type"] == "image"][0]
    assert block["source"]["media_type"] == "image/jpeg"
    assert block["source"]["data"] == "QUJD"


def test_attach_images_creates_user_when_absent():
    out = attach_images_to_messages([], ["AAAA"])
    assert out[-1]["role"] == "user"
    assert any(b["type"] == "image" for b in out[-1]["content"])


def test_messages_have_images():
    assert not messages_have_images([{"role": "user", "content": "hi"}])
    assert messages_have_images(attach_images_to_messages(
        [{"role": "user", "content": "x"}], ["AAAA"]))


@pytest.fixture
def router():
    from tools.llm.router import LLMRouter
    return LLMRouter()


def test_materialize_request_images(router):
    req = LLMRequest(messages=[{"role": "user", "content": "analyze"}], images=["AAAA"])
    router._materialize_request_images(req)
    assert req.images is None  # consumed
    assert messages_have_images(req.messages)
    assert router._request_has_images(req) is True


def test_vision_routing_promotes_vision_models(router):
    # minimax-m3 is an Ollama.com cloud model with supports_vision: true.
    chain = ["qwen3-local", "minimax-m3", "kimi-cloud"]
    req = LLMRequest(messages=[{"role": "user", "content": "x"}], images=["AAAA"])
    router._materialize_request_images(req)
    out = router._apply_vision_routing(chain, req)
    # a vision-capable model must lead
    first_cfg = router._get_model_config(out[0]) or {}
    assert first_cfg.get("supports_vision") is True
    assert "minimax-m3" in out  # ollama.com multimodal retained


def test_no_images_chain_unchanged(router):
    chain = ["qwen3-local", "minimax-m3"]
    req = LLMRequest(messages=[{"role": "user", "content": "no image"}])
    assert router._apply_vision_routing(chain, req) == chain


def test_vision_fallback_includes_ollama_cloud(router):
    """When the chain has no vision model, the vision category supplies one."""
    chain = ["qwen3-local"]  # assume text-only first
    req = LLMRequest(messages=[{"role": "user", "content": "x"}], images=["AAAA"])
    router._materialize_request_images(req)
    out = router._apply_vision_routing(chain, req)
    assert any((router._get_model_config(m) or {}).get("supports_vision") for m in out)
