"""Rotating egress-proxy resolution + application for LLM provider calls."""
import pytest

from tools.llm import proxy_resolver
from tools.llm.proxy_resolver import apply_llm_proxy, resolve_llm_proxy


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for var in (
        "ICDEV_LLM_PROXY",
        "ICDEV_LLM_PROXY_CMD",
        "ICDEV_LLM_PROXY_CMD_TTL",
        "HTTPS_PROXY",
        "https_proxy",
        "HTTP_PROXY",
        "http_proxy",
        "ALL_PROXY",
        "all_proxy",
    ):
        monkeypatch.delenv(var, raising=False)
    proxy_resolver._reset_for_tests()
    yield
    proxy_resolver._reset_for_tests()


def test_none_when_no_source():
    assert resolve_llm_proxy(None) is None
    assert resolve_llm_proxy({}) is None
    assert resolve_llm_proxy({"proxy": {"url": "", "command": ""}}) is None


def test_config_url(monkeypatch):
    assert resolve_llm_proxy({"proxy": {"url": "http://cfg:8080"}}) == "http://cfg:8080"


def test_env_beats_config(monkeypatch):
    monkeypatch.setenv("ICDEV_LLM_PROXY", "http://env:3128")
    assert resolve_llm_proxy({"proxy": {"url": "http://cfg:8080"}}) == "http://env:3128"


def test_command_beats_env(monkeypatch):
    monkeypatch.setenv("ICDEV_LLM_PROXY", "http://env:3128")
    # A cross-platform command that prints the current proxy.
    monkeypatch.setenv("ICDEV_LLM_PROXY_CMD", 'python -c "print(\'http://rotating:9999\')"')
    monkeypatch.setenv("ICDEV_LLM_PROXY_CMD_TTL", "0")
    assert resolve_llm_proxy({}) == "http://rotating:9999"


def test_command_ttl_cache(monkeypatch):
    # With a long TTL the command runs once; changing its output isn't seen yet.
    monkeypatch.setenv("ICDEV_LLM_PROXY_CMD", 'python -c "print(\'http://first:1\')"')
    monkeypatch.setenv("ICDEV_LLM_PROXY_CMD_TTL", "60")
    assert resolve_llm_proxy({}) == "http://first:1"
    monkeypatch.setenv("ICDEV_LLM_PROXY_CMD", 'python -c "print(\'http://second:2\')"')
    # cached — still the first value
    assert resolve_llm_proxy({}) == "http://first:1"


def test_command_failure_degrades_to_none(monkeypatch):
    monkeypatch.setenv("ICDEV_LLM_PROXY_CMD", "definitely-not-a-real-command-xyz --nope")
    monkeypatch.setenv("ICDEV_LLM_PROXY_CMD_TTL", "0")
    # broken resolver must never raise; falls through to None here
    assert resolve_llm_proxy({}) is None


def test_apply_sets_all_env_vars_and_reports_change():
    assert apply_llm_proxy("http://p:8080") is True
    import os

    assert os.environ["HTTPS_PROXY"] == "http://p:8080"
    assert os.environ["http_proxy"] == "http://p:8080"
    # same value again -> no change reported
    assert apply_llm_proxy("http://p:8080") is False
    # new value -> change reported
    assert apply_llm_proxy("http://p2:8080") is True


def test_apply_none_is_noop_and_does_not_clobber(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://preexisting:1")
    assert apply_llm_proxy(None) is False
    import os

    assert os.environ["HTTPS_PROXY"] == "http://preexisting:1"


def test_reset_client_hook_invalidates_cached_client():
    from tools.llm.provider import LLMProvider

    class _P(LLMProvider):
        def __init__(self):
            self._client = object()

        def provider_name(self):
            return "test"

        def invoke(self, request, model_id, model_config):
            return None

        def check_availability(self, model_id):
            return True

    p = _P()
    assert p._client is not None
    p.reset_client()
    assert p._client is None
