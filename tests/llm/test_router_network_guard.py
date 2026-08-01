"""Router choke point wires rate-limit gate + rotating proxy into every call.

Exercises ``LLMRouter._provider_invoke`` on a lightweight stand-in (binding the
unbound method to an object that only carries ``_config``) so we test the wiring
without a full, config-loading router init.
"""
import pytest

from tools.llm import proxy_resolver, rate_gate
from tools.llm.router import LLMRouter


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for var in (
        "ICDEV_LLM_RATE_LIMIT",
        "ICDEV_LLM_MAX_PARALLEL",
        "ICDEV_LLM_PAUSE_MIN",
        "ICDEV_LLM_PAUSE_MAX",
        "ICDEV_LLM_PROXY",
        "ICDEV_LLM_PROXY_CMD",
        "HTTPS_PROXY",
        "http_proxy",
    ):
        monkeypatch.delenv(var, raising=False)
    rate_gate._reset_for_tests()
    proxy_resolver._reset_for_tests()
    yield
    rate_gate._reset_for_tests()
    proxy_resolver._reset_for_tests()


class _Stub:
    """Carries only ``_config`` but borrows the router's choke-point methods.

    ``_enforce_routing_policy`` is borrowed too, not stubbed out: it is part of the
    choke point now, and a double that quietly skipped it would let this suite pass
    while the real chokepoint was broken.
    """

    _apply_network_guard = LLMRouter._apply_network_guard
    _enforce_routing_policy = LLMRouter._enforce_routing_policy
    _provider_invoke = LLMRouter._provider_invoke
    _provider_invoke_streaming = LLMRouter._provider_invoke_streaming

    def __init__(self, config):
        self._config = config


class _FakeProvider:
    def __init__(self):
        self._client = object()
        self.calls = []

    def invoke(self, request, model_id, model_cfg):
        self.calls.append((model_id, model_cfg))
        return "resp"

    def invoke_streaming(self, request, model_id, model_cfg):
        yield {"type": "text", "text": "hi"}

    def reset_client(self):  # real providers inherit this from LLMProvider
        self._client = None


def test_default_off_no_proxy_no_pause_no_reset(monkeypatch):
    slept = []
    monkeypatch.setattr(rate_gate.time, "sleep", lambda s: slept.append(s))
    stub = _Stub({"rate_limit": {"enabled": False}, "proxy": {}})
    prov = _FakeProvider()
    out = LLMRouter._provider_invoke(stub, prov, {}, "model-x", {"provider": "x"})
    assert out == "resp"
    assert prov.calls == [("model-x", {"provider": "x"})]
    assert slept == []              # no pause when disabled
    assert prov._client is not None  # no proxy -> client not reset


def test_rotating_proxy_applied_and_client_reset(monkeypatch):
    monkeypatch.setenv("ICDEV_LLM_PROXY", "http://rotating:8080")
    stub = _Stub({"rate_limit": {"enabled": False}, "proxy": {}})
    prov = _FakeProvider()
    LLMRouter._provider_invoke(stub, prov, {}, "m", {})
    import os

    assert os.environ["HTTPS_PROXY"] == "http://rotating:8080"
    assert prov._client is None  # proxy changed -> cached client invalidated


def test_enabled_pauses_between_calls(monkeypatch):
    monkeypatch.setenv("ICDEV_LLM_RATE_LIMIT", "true")
    slept = []
    monkeypatch.setattr(rate_gate.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(rate_gate.random, "uniform", lambda a, b: 3.7)
    stub = _Stub({"rate_limit": {"enabled": True}})
    prov = _FakeProvider()
    LLMRouter._provider_invoke(stub, prov, {}, "m", {})
    assert slept == [3.7]  # one randomized inter-call pause


def test_streaming_gated_holds_across_stream(monkeypatch):
    monkeypatch.setenv("ICDEV_LLM_RATE_LIMIT", "true")
    slept = []
    monkeypatch.setattr(rate_gate.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(rate_gate.random, "uniform", lambda a, b: 4.0)
    stub = _Stub({"rate_limit": {"enabled": True}})
    prov = _FakeProvider()
    stream = LLMRouter._provider_invoke_streaming(stub, prov, {}, "m", {})
    chunks = list(stream)  # consume — pause fires when generator exhausts
    assert chunks == [{"type": "text", "text": "hi"}]
    assert slept == [4.0]
